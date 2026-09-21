"""Pipeline orchestrator (§3): asyncio tasks + bounded queues per channel session.

Fixes from audit:
- Segment IDs use the DB auto-increment id (not a restarted counter).
- LLM calls run off the event loop via asyncio.to_thread.
- Speaker identification is wired through the pipeline.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC

import numpy as np

from app.asr.base import get_transcriber
from app.audio.segmenter import Segmenter
from app.config import settings
from app.ingest.supervisor import IngestSupervisor
from app.llm.base import get_llm
from app.memory.windowing import Utterance, WindowBuilder
from app.speakers.embedder import get_embedder


@dataclass
class PipelineConfig:
    session_id: int
    channel_id: int
    cmd: Sequence[str]
    reconnect: bool = True


def should_skip_window(importance: int | None, new_character: bool,
                       confidence: float | None = 1.0) -> bool:
    """Pure triage rule: skip only low-importance, high-confidence windows
    with no new person. Importance is 1-based (Jev levels are 0-based, +1);
    skipping <=2 drops idle banter and minor logistics. Low confidence (or
    missing) takes the full path — confidence-gated per TypeSafe docs,
    fail-open by construction."""
    imp = importance if isinstance(importance, int) else 3
    conf = confidence if isinstance(confidence, (int, float)) else 0.0
    return imp <= 2 and conf >= 0.6 and not new_character


def triage_window(text: str, known_characters: list[dict] | None = None,
                  open_threads: list[dict] | None = None) -> tuple[bool, int | None, float, bool, str]:
    """Jev triage gate: (idle, importance, confidence, new_character, model).

    Single fan-out call (importance + atomic signals, no candidates) meant
    to run inside asyncio.to_thread so it never blocks the loop. Fail-open:
    any error or non-Jev classifier returns idle=False (full path).
    """
    try:
        from app.classify.classifier import JevClassifier as _JC
        from app.classify.classifier import get_classifier as _gc
        _clf = _gc()
        if not isinstance(_clf, _JC):
            return False, None, 0.0, False, ""
        _v = _clf.triage(text, known_characters=known_characters,
                         open_threads=open_threads)
        return (should_skip_window(_v.importance, _v.new_character,
                                   _v.importance_confidence),
                _v.importance, _v.importance_confidence, _v.new_character, _v.model)
    except Exception as _e:
        print(f"[triage] error, fail open: {type(_e).__name__}", flush=True)
        return False, None, 0.0, False, ""


def reconcile_unobserved_gap(db, session_id: int, boot_epoch: int,
                             threshold_s: int = 180, now_iso: str | None = None):
    """Startup check: if the last closed window predates this boot by more
    than threshold_s, the hole was never observed by any worker (kill with
    no exit event). Record it so beat hour-bucketing can see the hole.
    Stream-seconds are boot-relative, so t_* are 0 and the wall range goes
    in reason. Returns the row or None. Pure DB read + one insert."""
    from datetime import datetime

    from sqlalchemy import func as _func
    from sqlalchemy import select as _s2

    from app.db import models as _m
    now = now_iso or datetime.now(UTC).isoformat()
    last = db.execute(_s2(_func.max(_m.Window.created_at)).where(
        _m.Window.session_id == session_id)).scalar()
    if not last:
        return None
    try:
        gap_s = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
    except Exception:
        return None
    if gap_s <= threshold_s:
        return None
    row = _m.IngestGap(session_id=session_id, t_start=0, t_end=0,
                       reason=f"unobserved worker-down {last}..{now}",
                       boot_epoch=boot_epoch)
    db.add(row)
    db.commit()
    return row


async def run_session(cfg: PipelineConfig, db_factory, bus=None) -> None:
    """Live/replay pipeline: ingest -> VAD -> ASR+speaker -> windows -> extraction."""
    import time as _time

    from app.db.session import ensure_schema as _ensure_schema
    # Respect isolated/eval session factories instead of always migrating the
    # process-global database.
    _bind = getattr(db_factory, "kw", {}).get("bind")
    _ensure_schema(_bind)
    # Worker-boot unix time: disambiguates stream-second timestamps across restarts.
    boot_epoch = int(_time.time())
    # Startup reconciliation: record unobserved downtime from a previous kill.
    try:
        _rdb = db_factory()
        try:
            _gap = reconcile_unobserved_gap(_rdb, cfg.session_id, boot_epoch)
            if _gap is not None:
                print(f"[ingest] unobserved downtime recorded: {_gap.reason}", flush=True)
        finally:
            _rdb.close()
    except Exception as _e:
        print(f"[ingest] reconcile skipped: {type(_e).__name__}", flush=True)
    seg_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    utt_q: asyncio.Queue = asyncio.Queue(maxsize=200)
    transcriber = get_transcriber()
    llm = get_llm("extract")
    segmenter = Segmenter(use_silero=True)
    builder = WindowBuilder()
    embedder = get_embedder(settings.SPK_EMBED_MODEL)
    centroids: dict[int, np.ndarray] = {}
    provisional: dict[str, dict[str, object]] = {}
    prov_counter = 0

    def _now_iso() -> str:
        from datetime import datetime
        return datetime.now(UTC).isoformat()

    def _normalize(vec: np.ndarray) -> np.ndarray:
        v = np.asarray(vec, dtype="float32")
        n = float(np.linalg.norm(v))
        return (v / n).astype("float32") if n > 0 else v

    def _load_speaker_library() -> None:
        from sqlalchemy import select as _s2

        from app.consent import has_consent
        from app.db import models as _m
        db = db_factory()
        try:
            rows = db.execute(
                _s2(_m.SpeakerEmbedding)
                .join(_m.Speaker, _m.Speaker.id == _m.SpeakerEmbedding.speaker_id)
                .where(
                    _m.Speaker.channel_id == cfg.channel_id,
                    _m.SpeakerEmbedding.model == embedder.model_name,
                    _m.SpeakerEmbedding.dim == embedder.dim,
                )
                .order_by(_m.SpeakerEmbedding.speaker_id, _m.SpeakerEmbedding.id.desc())
            ).scalars().all()
            seen: set[int] = set()
            for row in rows:
                if row.speaker_id in seen:
                    continue
                if settings.CONSENT_REQUIRED and not has_consent(db, row.speaker_id):
                    continue
                seen.add(row.speaker_id)
                if len(row.embedding) % np.dtype(np.float32).itemsize:
                    continue
                vec = np.frombuffer(row.embedding, dtype=np.float32).astype("float32").copy()
                if vec.shape == (embedder.dim,):
                    centroids[row.speaker_id] = _normalize(vec)
        finally:
            db.close()

    _load_speaker_library()

    def _identify_speaker(
        pcm: np.ndarray, duration: float
    ) -> tuple[int | None, float | None, dict | None, str]:
        """Identify a speaker without creating permanent rows for weak evidence.

        Returns (speaker_id, confidence, promotion, flags). Promotion is only
        set when a provisional voice cluster has earned a permanent Speaker row.
        """
        try:
            emb = embedder.embed(pcm)
        except Exception:
            return None, None, None, "embed-failed"
        from app.speakers.embedder import match_speaker
        if duration < settings.SPK_MIN_SEG_SECONDS:
            kind, conf, ref = match_speaker(
                emb, centroids,
                settings.SPK_MATCH_THRESHOLD, settings.SPK_MARGIN,
                settings.SPK_CLUSTER_THRESHOLD, {})
            if kind == "matched" and ref.startswith("speaker:"):
                return int(ref.split(":", 1)[1]), conf, None, ""
            return None, None, None, "too-short"
        unknown_map = {k: v["emb"] for k, v in provisional.items()}
        kind, conf, ref = match_speaker(
            emb, centroids,
            settings.SPK_MATCH_THRESHOLD, settings.SPK_MARGIN,
            settings.SPK_CLUSTER_THRESHOLD, unknown_map)
        if kind == "matched" and ref.startswith("speaker:"):
            sid = int(ref.split(":", 1)[1])
            old = centroids.get(sid)
            if old is not None:
                centroids[sid] = _normalize(0.9 * old + 0.1 * emb)
            return sid, conf, None, ""
        if kind == "unknown" and ref.startswith("unknown:"):
            key = ref.split(":", 1)[1]
            st = provisional.get(key)
            if st is None:
                return None, None, None, "provisional-missing"
            st["count"] = int(st["count"]) + 1
            st["seconds"] = float(st["seconds"]) + duration
            st["emb"] = _normalize(0.8 * np.asarray(st["emb"]) + 0.2 * emb)
            if (int(st["count"]) >= settings.SPK_PROMOTE_MIN_SEGMENTS
                    or float(st["seconds"]) >= settings.SPK_PROMOTE_MIN_SECONDS):
                # Unknown voices may cluster in process memory, but biometric
                # embeddings are never persisted without explicit consent.
                if settings.CONSENT_REQUIRED:
                    return None, None, None, "consent-required"
                return None, conf, {
                    "key": key,
                    "emb": st["emb"],
                    "seconds": float(st["seconds"]),
                    "count": int(st["count"]),
                    "conf": conf,
                }, "promote"
            return None, None, None, "provisional"
        nonlocal prov_counter
        prov_counter += 1
        key = f"p{prov_counter}"
        provisional[key] = {"emb": emb, "count": 1, "seconds": duration}
        while len(provisional) > settings.SPK_MAX_PROVISIONAL:
            provisional.pop(next(iter(provisional)))
        return None, None, None, "provisional"

    async def ingest_task():
        sup = IngestSupervisor(cfg.cmd, on_gap=_write_gap_now,
                               reconnect=cfg.reconnect,
                               offline_timeout_seconds=(
                                   settings.SESSION_END_OFFLINE_MINUTES * 60
                                   if cfg.reconnect else None))
        async for chunk in sup.frames():
            pcm = np.frombuffer(bytes(chunk), dtype=np.int16).astype("float32") / 32768.0
            t0 = sup.t_now - len(pcm) / 16000
            for s in segmenter.add_chunk(pcm, t0):
                await seg_q.put(s)
            if sup.gaps:
                _drain_gaps(sup)
        for s in segmenter.flush(sup.t_now):
            await seg_q.put(s)
        await seg_q.put(None)

    def _write_gap_now(g) -> None:
        # Synchronous insert at detection time: a kill loses at most this row.
        from app.db import models as _m
        db = db_factory()
        try:
            db.add(_m.IngestGap(session_id=cfg.session_id, t_start=g.t_start,
                                t_end=g.t_end, reason=g.reason, boot_epoch=boot_epoch))
            db.commit()
            g.written = True
        except Exception:
            pass
        finally:
            db.close()

    def _drain_gaps(sup) -> None:
        # Retry path for gaps whose synchronous write failed; then clear.
        from app.db import models as _m
        db = db_factory()
        try:
            for g in sup.gaps:
                if g.written:
                    continue
                db.add(_m.IngestGap(session_id=cfg.session_id, t_start=g.t_start,
                                    t_end=g.t_end, reason=g.reason, boot_epoch=boot_epoch))
                g.written = True
            db.commit()
            sup.gaps.clear()
        finally:
            db.close()

    async def asr_task():
        from sqlalchemy import select as _s2

        from app.db import models as _m
        while True:
            s = await seg_q.get()
            try:
                if s is None:
                    await utt_q.put(None)
                    return
                words = await asyncio.to_thread(
                    transcriber.transcribe, s.pcm, s.t_start, "")
                text = " ".join(w.text for w in words)
                if not text.strip():
                    continue
                duration = max(0.0, float(s.t_end - s.t_start))
                speaker_id, speaker_conf, promotion, flags = await asyncio.to_thread(
                    _identify_speaker, s.pcm, duration)
                db = db_factory()
                try:
                    if promotion is not None:
                        emb = np.asarray(promotion["emb"], dtype="float32")
                        spk = _m.Speaker(
                            channel_id=cfg.channel_id, name="pending", role="unknown",
                            confirmed=0, speech_seconds=float(promotion.get("seconds", duration)),
                            created_at=_now_iso())
                        db.add(spk)
                        db.flush()
                        spk.name = f"speaker_{spk.id}"
                        db.add(_m.SpeakerEmbedding(
                            speaker_id=spk.id, embedding=emb.tobytes(),
                            dim=int(emb.shape[0]), model=embedder.model_name,
                            seconds=float(promotion.get("seconds", duration)),
                            source="pipeline", created_at=_now_iso()))
                        centroids[spk.id] = _normalize(emb)
                        provisional.pop(promotion.get("key", ""), None)
                        speaker_id = spk.id
                        speaker_conf = float(promotion.get("conf") or 0.0)
                        flags = ""
                    elif speaker_id is not None:
                        spk = db.get(_m.Speaker, speaker_id)
                        if spk is None or spk.channel_id != cfg.channel_id:
                            centroids.pop(speaker_id, None)
                            speaker_id = None
                            speaker_conf = None
                            flags = "speaker-missing"
                        else:
                            spk.speech_seconds = float(spk.speech_seconds or 0.0) + duration
                            if duration >= settings.SPK_MIN_SEG_SECONDS:
                                current = centroids.get(speaker_id)
                                if current is not None:
                                    emb_row = db.execute(
                                        _s2(_m.SpeakerEmbedding).where(
                                            _m.SpeakerEmbedding.speaker_id == speaker_id,
                                            _m.SpeakerEmbedding.model == embedder.model_name,
                                            _m.SpeakerEmbedding.dim == embedder.dim,
                                        ).order_by(_m.SpeakerEmbedding.id.desc())
                                    ).scalars().first()
                                    if emb_row is None:
                                        db.add(_m.SpeakerEmbedding(
                                            speaker_id=speaker_id,
                                            embedding=np.asarray(current, dtype="float32").tobytes(),
                                            dim=embedder.dim, model=embedder.model_name,
                                            seconds=duration, source="pipeline",
                                            created_at=_now_iso()))
                                    else:
                                        emb_row.embedding = np.asarray(current, dtype="float32").tobytes()
                                        emb_row.seconds = float(emb_row.seconds or 0.0) + duration
                    seg = _m.Segment(
                        session_id=cfg.session_id, t_start=s.t_start, t_end=s.t_end,
                        wall_start=None, text=text,
                        words_json="[]",
                        asr_model=getattr(transcriber, "model_name", "?"),
                        speaker_id=speaker_id, speaker_conf=speaker_conf,
                        speaker_flags=flags)
                    db.add(seg)
                    db.commit()
                    db.refresh(seg)
                    db_seg_id = seg.id
                finally:
                    db.close()
                await utt_q.put(Utterance(id=db_seg_id, t_start=s.t_start,
                                           t_end=s.t_end, text=text,
                                           words=len(text.split()),
                                           speaker=str(speaker_id) if speaker_id else "",
                                           speaker_conf=speaker_conf))
                if bus:
                    await bus.publish("transcript.segment",
                                       {"t_start": s.t_start, "text": text})
            finally:
                seg_q.task_done()

    async def window_task():
        from datetime import datetime

        from sqlalchemy import select as _s2

        from app.db import models as m
        from app.memory.extractor import run_extraction
        prev_tail: list[dict] = []
        while True:
            u = await utt_q.get()
            try:
                stopping = u is None
                w = builder.flush() if stopping else builder.add(u)
                if w is None:
                    if stopping:
                        return
                    continue
                db = db_factory()
                try:
                    wrow = m.Window(session_id=cfg.session_id, t_start=w.t_start,
                                     t_end=w.t_end, status="extracting",
                                     boot_epoch=boot_epoch,
                                     created_at=datetime.now(UTC).isoformat())
                    db.add(wrow)
                    db.commit()
                    db.refresh(wrow)
                    window_id = wrow.id
                    segment_ids = [x.id for x in w.utterances]
                    if segment_ids:
                        db.execute(m.Segment.__table__.update().where(
                            m.Segment.id.in_(segment_ids)).values(window_id=window_id))
                        db.commit()
                    utts = prev_tail + [{"id": x.id, "t_start": x.t_start,
                                          "t_end": x.t_end, "text": x.text,
                                          "speaker": x.speaker or "unknown",
                                          "conf": x.speaker_conf}
                                         for x in w.utterances]
                    known_characters = [
                        {"name": e.canonical_name, "desc": e.description or ""}
                        for e in db.execute(_s2(m.Entity).where(
                            m.Entity.channel_id == cfg.channel_id).order_by(
                            m.Entity.mention_count.desc()).limit(50)).scalars().all()
                    ]
                    open_threads = [
                        {"title": t.title, "summary": t.summary or ""}
                        for t in db.execute(_s2(m.Thread).where(
                            m.Thread.channel_id == cfg.channel_id,
                            m.Thread.status == "open").order_by(
                            m.Thread.last_updated_at.desc()).limit(20)).scalars().all()
                    ]
                finally:
                    db.close()
                # Jev triage gate (§5.11/M7): fast importance check in a
                # worker thread (never blocks the loop). Idle windows skip
                # the expensive extraction + rolling calls. Fail-open: any
                # triage error runs the full path.
                _skipped, _timp, _tconf, _tnew, _tmodel = await asyncio.to_thread(
                    triage_window, "\n".join(x["text"] for x in utts),
                    known_characters, open_threads)
                if _skipped:
                    db0 = db_factory()
                    try:
                        _wskip = db0.get(m.Window, window_id)
                        if _wskip is not None:
                            _wskip.status = "skipped-idle"
                            db0.commit()
                    finally:
                        db0.close()
                    print(f"[triage] window {window_id}: idle "
                          f"(imp={_timp} conf={_tconf:.2f} new={_tnew} model={_tmodel})"
                          f" -> skip extraction", flush=True)
                else:
                    print(f"[triage] window {window_id}: extract "
                          f"(imp={_timp} conf={_tconf:.2f} new={_tnew} model={_tmodel})",
                          flush=True)
                    db_extract = db_factory()
                    try:
                        await asyncio.to_thread(
                            run_extraction, db_extract, cfg.channel_id, cfg.session_id,
                            window_id, utts, llm, settings.LLM_MODEL_EXTRACT)
                    finally:
                        db_extract.close()
                # Rolling summary (§5.10) — skipped only for idle windows.
                if not _skipped:
                    try:
                        from app.summarize.rolling import save_summary, update_rolling
                        db3 = db_factory()
                        try:
                            prev = db3.execute(
                                _s2(m.Summary).where(
                                    m.Summary.session_id == cfg.session_id,
                                    m.Summary.kind == "rolling").order_by(
                                    m.Summary.id.desc())).scalars().first()
                            wdone = db3.get(m.Window, window_id)
                            ev_rows = db3.execute(
                                _s2(m.Event).where(
                                    m.Event.window_id == window_id)).scalars().all()
                            from app.llm.budget import (
                                budget_status,
                                should_skip_extraction,
                            )
                            month_prefix = datetime.now(UTC).strftime("%Y-%m")
                            budget = budget_status(
                                db3, cfg.session_id, month_prefix,
                                settings.LLM_SESSION_BUDGET_USD,
                                settings.LLM_MONTHLY_BUDGET_USD)
                            skip_summary, _ = should_skip_extraction(budget)
                            if skip_summary:
                                new_text, rolling_prompt = "", ""
                            else:
                                new_text, rolling_prompt = update_rolling(
                                    llm, prev.text if prev else "",
                                    (wdone.window_summary or "") if wdone else "",
                                    [e.description for e in ev_rows], include_prompt=True)
                            if new_text.strip():
                                save_summary(db3, cfg.session_id, "rolling", new_text,
                                             t0=w.t_start, t1=w.t_end,
                                             model=settings.LLM_MODEL_EXTRACT,
                                             input_text=rolling_prompt)
                                from app.observability import clear_session_error
                                clear_session_error(db3, cfg.session_id, "rolling")
                                db3.commit()
                        finally:
                            db3.close()
                    except Exception as exc:
                        from app.observability import (
                            report_pipeline_error,
                            set_session_error,
                        )
                        report_pipeline_error("rolling", exc)
                        err_db = db_factory()
                        try:
                            set_session_error(err_db, cfg.session_id, "rolling", exc)
                            err_db.commit()
                        finally:
                            err_db.close()
                        if bus:
                            await bus.publish(
                                "pipeline.error",
                                {"stage": "rolling", "window_id": window_id,
                                 "error": type(exc).__name__})
                prev_tail = utts[-2:]
                if bus:
                    await bus.publish("summary.updated", {"window_id": window_id})
                if stopping:
                    return
            finally:
                utt_q.task_done()

    final_status = "ended"
    fatal_exc: Exception | None = None
    try:
        await asyncio.gather(ingest_task(), asr_task(), window_task())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        final_status = "failed"
        fatal_exc = exc
        from app.observability import report_pipeline_error
        report_pipeline_error("session", exc)
        raise
    finally:
        from datetime import datetime

        from app.db import models as _m
        db = db_factory()
        try:
            session = db.get(_m.Session, cfg.session_id)
            if session is not None:
                session.status = final_status
                session.ended_at = datetime.now(UTC).isoformat()
                if fatal_exc is not None:
                    from app.observability import set_session_error
                    set_session_error(db, cfg.session_id, "session", fatal_exc)
                db.commit()
                from app.retention import enforce_retention
                enforce_retention(db)
        finally:
            db.close()
        if bus:
            await bus.publish("session.status", {"status": final_status})

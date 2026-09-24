"""Summarizer (§5.10): rolling (small/fast), recap + final (stronger), from threads+events."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.llm import prompts
from app.memory.speaker_names import replace_known_speaker_ids, speaker_names


def update_rolling(llm, prev: str, window_summary: str, events: list[str],
                   include_prompt: bool = False) -> str | tuple[str, str]:
    import re
    # Old rolling rows contain a bullets format the prompt no longer asks
    # for — strip markdown fragments so the model doesn't copy the pattern.
    prev_clean = re.split(r"\*+", prev)[0].strip()
    prompt = f"Previous rolling summary:\n{prev_clean}\n\nNew window summary:\n{window_summary}\n\nNew events:\n" + "\n".join(events[:10])
    text = llm.generate_text(prompt, system=prompts.ROLLING_SYSTEM, max_tokens=150)
    result = " ".join(text.split()[:30])
    return (result, prompt) if include_prompt else result


def build_recap(db: SASession, session_id: int, llm, max_words: int = 100,
                 include_prompt: bool = False, full: bool = False) -> str | tuple[str, str]:
    sess = db.get(m.Session, session_id)
    # Open threads are channel-wide memory; many have not appeared this stream.
    # Only a catch-up recap should use them as context. A full summary must
    # describe this session, not import old plans from another session.
    threads = []
    if not full:
        thq = select(m.Thread).where(m.Thread.status != "resolved")
        if sess is not None:
            thq = thq.where(m.Thread.channel_id == sess.channel_id)
        threads = db.execute(
            thq.order_by(m.Thread.last_updated_at.desc(), m.Thread.id.desc()).limit(6)
        ).scalars().all()
    # Recency first: take the latest events, then rank those by importance so
    # old pivotal events can't dominate the recap forever.
    recent = db.execute(select(m.Event).where(m.Event.session_id == session_id)
                        .order_by(m.Event.id.desc()).limit(60)).scalars().all()
    selected = sorted(recent, key=lambda e: (e.importance or 0, e.id), reverse=True)[:30]
    # Selection favors important recent events, but presentation is strictly
    # newest-first so the model receives an honest chronology.
    events = sorted(selected, key=lambda e: e.id, reverse=True)
    prev_recaps = db.execute(select(m.Summary).where(
        m.Summary.session_id == session_id, m.Summary.kind == ("full" if full else "recap"))
        .order_by(m.Summary.id.desc()).limit(1)).scalars().all()
    # Older full summaries may already contain channel-wide thread leakage.
    # The v4 key also retires summaries that printed raw speaker IDs.
    trusted_previous = bool(prev_recaps) and (not full or
        (prev_recaps[0].cache_key or "").startswith("session-v4:"))
    prev_text = prev_recaps[0].text if trusted_previous else ""
    prev_event_max = 0
    if trusted_previous and not full and prev_recaps[0].cache_key:
        try:
            key = prev_recaps[0].cache_key
            prev_event_max = int(key.split(":", 1)[0])
        except (TypeError, ValueError):
            pass
    tpart = "\n".join(f"- {t.title}: {t.summary or ''}" for t in threads)
    def _clock(seconds: float | None) -> str:
        total = max(0, int(seconds or 0))
        return f"{total // 60:02d}:{total % 60:02d}"

    epart = "\n".join(
        f"- {'[NEW] ' if e.id > prev_event_max and prev_event_max else ''}"
        f"[{_clock(e.t_start)} | importance {e.importance} | {e.type}] {e.description}"
        for e in events)
    context = "" if full else f"Open threads (context, not necessarily new):\n{tpart or '(none)'}\n\n"
    prompt = (f"{context}Events (newest first, with stream timestamps):\n{epart or '(none)'}\n\n"
              f"Write a ≤{max_words}-word catch-up recap grouped by storyline, "
              f"emphasizing the most recent developments.")
    if full:
        beats = db.execute(select(m.PlotBeat).where(m.PlotBeat.session_id == session_id)
                           .order_by(m.PlotBeat.hour_index, m.PlotBeat.id).limit(120)).scalars().all()
        history = "\n".join(f"- Hour {b.hour_index + 1}: {b.headline}"
                            for b in beats)
        # On the first request, the stream may already be hours old. Include
        # its earliest events if hourly beats have not been produced yet.
        early = ""
        if not prev_text and not beats:
            oldest = db.execute(select(m.Event).where(m.Event.session_id == session_id)
                                .order_by(m.Event.id).limit(20)).scalars().all()
            recent_ids = {e.id for e in events}
            early = "\n".join(f"- {e.description}" for e in oldest if e.id not in recent_ids)
        prompt += (f"\n\nPrevious cumulative full summary (use for earlier developments, "
                   f"not as a template for length or wording):\n"
                   f"{prev_text or '(none yet)'}\n\nEarlier timeline highlights:\n"
                   f"{history or early or '(none yet)'}\n\nWrite a self-contained overview of "
                   f"the entire stream so far. Identify distinct activities before writing; "
                   f"give each supported activity a brief place, then say where it stands now. "
                   f"If there is only one activity, keep it short rather than expanding its "
                   f"codes, intermediate guesses, or repetitive steps. If little happened, "
                   f"use fewer than {max_words} words.")
    elif prev_text:
        prompt += (f"\n\nThe user already saw this previous catch-up:\n{prev_text}\n\n"
                   f"Lead with entries marked NEW since that recap; do not restate old "
                   f"material at length. If no entry is marked NEW, say that there is no "
                   f"meaningful update yet instead of recycling the previous recap.")
    # Confidence feedback loop: recent No / Not Sure votes simplify the style.
    fb = db.execute(select(m.SummaryFeedback.vote).where(
        m.SummaryFeedback.session_id == session_id,
        m.SummaryFeedback.kind == "recap")
        .order_by(m.SummaryFeedback.id.desc()).limit(10)).scalars().all()
    neg = sum(1 for v in fb if v in ("no", "not_sure"))
    if fb and (neg >= 2 or (len(fb) <= 2 and neg == len(fb))):
        prompt += ("\n\nRecent readers found recaps hard to scan: use very short, "
                   "simple sentences and plain words.")
    if sess is not None:
        prompt = replace_known_speaker_ids(prompt, speaker_names(db, sess.channel_id))
    result = llm.generate_text(prompt, system=prompts.FULL_SUMMARY_SYSTEM if full else prompts.RECAP_SYSTEM,
                               max_tokens=550 if full else 350)
    if sess is not None:
        result = replace_known_speaker_ids(result, speaker_names(db, sess.channel_id))
    return (result, prompt) if include_prompt else result


def save_summary(db: SASession, session_id: int, kind: str, text: str,
                 t0: float | None = None, t1: float | None = None, model: str = "",
                 input_text: str = "") -> m.Summary:
    from app.config import settings
    from app.llm.budget import estimate_cost_usd
    tokens_in = max(1, len(input_text) // 4)
    tokens_out = max(1, len(text) // 4)
    s = m.Summary(session_id=session_id, kind=kind, covers_t_start=t0, covers_t_end=t1,
                  text=text, model=model, tokens_in=tokens_in, tokens_out=tokens_out,
                  cost_usd=estimate_cost_usd(
                      tokens_in, tokens_out, settings.LLM_COST_PER_1K_IN,
                      settings.LLM_COST_PER_1K_OUT),
                  created_at=datetime.now(UTC).isoformat())
    db.add(s)
    db.commit()
    return s

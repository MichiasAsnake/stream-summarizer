"""Stream timeline: hourly plot beats plus live highlights for the open hour.

Hours are wall-clock hours since the session started (stream-second
timestamps restart when the worker restarts, wall time does not). When an
hour closes, one LLM call turns its events into 1–4 one-line beats, stored
in plot_beats; beat_runs makes that exactly-once per hour. Hours without
beats yet (the current hour, or before the generator has run) show their
most important events directly.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m
from app.llm import prompts

log = logging.getLogger(__name__)

PROMPT_VERSION = "beats-v1"
HOUR = 3600
CLOSE_GRACE = timedelta(minutes=3)  # let the hour's last window finish extracting
LIVE_MIN_IMPORTANCE = 4
LIVE_PER_HOUR = 3
BEAT_MIN_IMPORTANCE = 3


class BeatOut(BaseModel):
    headline: str = Field(description="One line, at most 12 words, plain and factual.")
    significance: int = Field(ge=1, le=5)
    event_ids: list[int] = Field(description="Ids of the events this beat covers.")


class BeatsOut(BaseModel):
    beats: list[BeatOut] = Field(default_factory=list, max_length=4)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}"


def timed_events(db: SASession, session_id: int) -> list[tuple[float, m.Event]]:
    """(seconds since stream start, event), chronological. The wall time of an
    event is its window's close time minus how far before the window end it began."""
    sess = db.get(m.Session, session_id)
    start = _dt(sess.started_at) if sess else None
    if start is None:
        return []
    rows = db.execute(select(m.Event, m.Window).join(m.Window, m.Window.id == m.Event.window_id)
                      .where(m.Event.session_id == session_id)).all()
    out = []
    for ev, win in rows:
        closed = _dt(win.created_at)
        if closed is None:
            continue
        back = 0.0
        if ev.t_start is not None and win.t_end is not None:
            back = min(max(win.t_end - ev.t_start, 0.0), max(win.t_end - win.t_start, 0.0))
        out.append((max((closed - start).total_seconds() - back, 0.0), ev))
    return sorted(out, key=lambda x: (x[0], x[1].id))


def closed_hours(sess: m.Session, now: datetime) -> int:
    """Number of fully closed hours (all hours once the session has ended)."""
    start = _dt(sess.started_at)
    if start is None:
        return 0
    end = _dt(sess.ended_at) if sess.status not in ("starting", "live", "degraded") else None
    if end is not None:
        return int((end - start).total_seconds() // HOUR) + 1
    return max(int(((now - CLOSE_GRACE) - start).total_seconds() // HOUR), 0)


def generate_hour(db: SASession, session_id: int, hour: int, llm,
                  events: list[tuple[float, m.Event]] | None = None) -> list[m.PlotBeat]:
    """Create beats for one closed hour, exactly once. Returns created beats."""
    from app.observability import report_pipeline_error

    now = datetime.now(UTC).isoformat()
    events = timed_events(db, session_id) if events is None else events
    in_hour = [(t, e) for t, e in events if int(t // HOUR) == hour
               and (e.importance or 0) >= BEAT_MIN_IMPORTANCE]

    def mark(status: str, reason: str | None) -> bool:
        db.add(m.BeatRun(session_id=session_id, hour_index=hour, run_label="live",
                         status=status, reason=reason, created_at=now))
        try:
            db.commit()
            return True
        except IntegrityError:  # another worker already did this hour
            db.rollback()
            return False

    if not in_hour:
        mark("skipped", "no notable events")
        return []
    earlier = db.execute(select(m.PlotBeat.headline).where(
        m.PlotBeat.session_id == session_id, m.PlotBeat.hour_index < hour)
        .order_by(m.PlotBeat.hour_index.desc(), m.PlotBeat.t_start.desc()).limit(4)).scalars().all()
    lines = "\n".join(f"- id {e.id} [{clock(t)} | importance {e.importance}] {e.description}"
                      for t, e in in_hour)
    prompt = (f"Stream hour {hour + 1}. Events (chronological, elapsed stream time):\n{lines}\n\n"
              + ("Earlier timeline entries (do not repeat):\n"
                 + "\n".join(f"- {h}" for h in reversed(earlier)) + "\n\n" if earlier else "")
              + "Write 1-4 timeline beats for this hour.")
    try:
        raw = llm.generate_json(BeatsOut.model_json_schema(), prompt, system=prompts.BEATS_SYSTEM)
        out = BeatsOut.model_validate(raw)
    except Exception as exc:
        report_pipeline_error("beats", exc)
        log.warning("beats for session %s hour %s failed: %s", session_id, hour,
                    type(exc).__name__)
        return []  # no watermark: retried on the next tick
    by_id = {e.id: t for t, e in in_hour}
    if not mark("done", None):
        return []
    created = []
    for b in out.beats[:4]:
        ids = [i for i in b.event_ids if i in by_id]
        headline = b.headline.strip()
        if not ids or not headline:
            continue  # a beat must be grounded in this hour's events
        times = [by_id[i] for i in ids]
        beat = m.PlotBeat(session_id=session_id, hour_index=hour, run_label="live",
                          t_start=min(times), t_end=max(times), headline=headline[:160],
                          significance=b.significance, status="final",
                          prompt_version=PROMPT_VERSION, model=settings.LLM_MODEL_RECAP,
                          tokens_in=max(1, len(prompt) // 4),
                          tokens_out=max(1, len(headline) // 4), created_at=now)
        db.add(beat)
        db.flush()
        for i in ids:
            db.add(m.BeatEvent(beat_id=beat.id, event_id=i))
        created.append(beat)
    db.commit()
    return created


def due_hours(db: SASession, now: datetime, max_age_hours: float = 24) -> list[tuple[int, int]]:
    """(session_id, hour) pairs that are closed but have no beat run yet."""
    cutoff = (now - timedelta(hours=max_age_hours)).isoformat()
    sessions = db.execute(select(m.Session).where(
        (m.Session.status.in_(("starting", "live", "degraded")))
        | (m.Session.ended_at >= cutoff))).scalars().all()
    due = []
    for s in sessions:
        done = set(db.execute(select(m.BeatRun.hour_index).where(
            m.BeatRun.session_id == s.id, m.BeatRun.run_label == "live")).scalars())
        due += [(s.id, h) for h in range(closed_hours(s, now)) if h not in done]
    return due


def generate_due_beats(db_factory, llm_factory, now: datetime | None = None,
                       limit: int = 3) -> int:
    """Generate beats for up to `limit` closed hours. Returns beats created."""
    from app.llm.budget import budget_status, should_skip_extraction
    now = now or datetime.now(UTC)
    db = db_factory()
    try:
        created = 0
        for sid, hour in due_hours(db, now)[:limit]:
            budget = budget_status(db, sid, now.strftime("%Y-%m"),
                                   settings.LLM_SESSION_BUDGET_USD,
                                   settings.LLM_MONTHLY_BUDGET_USD)
            if should_skip_extraction(budget)[0]:
                continue
            created += len(generate_hour(db, sid, hour, llm_factory()))
        return created
    finally:
        db.close()


def timeline(db: SASession, session_id: int) -> list[dict]:
    """Beats for hours that have them; top events for hours that don't yet."""
    beats = db.execute(select(m.PlotBeat).where(
        m.PlotBeat.session_id == session_id, m.PlotBeat.run_label == "live")).scalars().all()
    runs = set(db.execute(select(m.BeatRun.hour_index).where(
        m.BeatRun.session_id == session_id, m.BeatRun.run_label == "live")).scalars())
    items = [{"at": b.t_start or 0.0, "clock": clock(b.t_start or 0.0), "hour": b.hour_index,
              "headline": b.headline, "significance": b.significance, "kind": "beat"}
             for b in beats]
    pending: dict[int, list[tuple[float, m.Event]]] = {}
    for t, e in timed_events(db, session_id):
        h = int(t // HOUR)
        if h not in runs and (e.importance or 0) >= LIVE_MIN_IMPORTANCE:
            pending.setdefault(h, []).append((t, e))
    for h, evs in pending.items():
        top = sorted(evs, key=lambda x: (x[1].importance, x[0]), reverse=True)[:LIVE_PER_HOUR]
        items += [{"at": t, "clock": clock(t), "hour": h, "headline": e.description,
                   "significance": e.importance, "kind": "event"} for t, e in top]
    return sorted(items, key=lambda i: i["at"])

"""End-of-stream summaries for sessions that ended or were interrupted.

Run from the auto-monitor loop rather than the pipeline's shutdown path, so
a stop or crash never blocks on an LLM call and a failed attempt is retried
on a later tick.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m
from app.llm import prompts

log = logging.getLogger(__name__)

FINAL_STATUSES = ("ended", "interrupted")
MAX_ATTEMPTS = 3
_attempts: dict[int, int] = {}


def pending_sessions(db: SASession, limit: int = 2, max_age_hours: float = 24) -> list[int]:
    """Recently finished sessions with at least one event and no final summary."""
    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
    has_events = exists().where(m.Event.session_id == m.Session.id)
    rows = db.execute(select(m.Session.id).where(
        m.Session.status.in_(FINAL_STATUSES), m.Session.final_summary.is_(None),
        m.Session.ended_at >= cutoff, has_events)
        .order_by(m.Session.id.desc())).scalars().all()
    return [sid for sid in rows if _attempts.get(sid, 0) < MAX_ATTEMPTS][:limit]


def build_final_prompt(db: SASession, session_id: int) -> str:
    sess = db.get(m.Session, session_id)
    events = db.execute(select(m.Event).where(m.Event.session_id == session_id)
                        .order_by(m.Event.importance.desc(), m.Event.id.desc())
                        .limit(40)).scalars().all()
    events = sorted(events, key=lambda e: (e.t_start or 0, e.id))  # chronological
    threads = db.execute(select(m.Thread).where(m.Thread.channel_id == sess.channel_id)
                         .order_by(m.Thread.last_updated_at.desc()).limit(8)).scalars().all()

    def clock(sec: float | None) -> str:
        t = max(0, int(sec or 0))
        return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"

    head = []
    if sess.title:
        head.append(f"Stream title: {sess.title}")
    if sess.category:
        head.append(f"Category: {sess.category}")
    epart = "\n".join(f"- [{clock(e.t_start)} | importance {e.importance}] {e.description}"
                      for e in events)
    tpart = "\n".join(f"- {t.title} ({t.status}): {t.summary or ''}" for t in threads)
    return ("\n".join(head) + "\n\n" if head else "") + (
        f"Events (chronological, with stream timestamps):\n{epart or '(none)'}\n\n"
        f"Storylines at the end of the stream:\n{tpart or '(none)'}\n\n"
        "Write the end-of-stream wrap-up.")


def finalize_session(db: SASession, session_id: int, llm) -> str | None:
    """Generate and store the final summary. Returns the text, or None when
    skipped (budget) or failed (retried on a later tick)."""
    from app.llm.budget import budget_status, should_skip_extraction
    from app.observability import report_pipeline_error
    from app.summarize.rolling import save_summary

    budget = budget_status(db, session_id, datetime.now(UTC).strftime("%Y-%m"),
                           settings.LLM_SESSION_BUDGET_USD, settings.LLM_MONTHLY_BUDGET_USD)
    skip, reason = should_skip_extraction(budget)
    if skip:
        log.info("final summary for session %s skipped: %s", session_id, reason)
        _attempts[session_id] = MAX_ATTEMPTS
        return None
    _attempts[session_id] = _attempts.get(session_id, 0) + 1
    prompt = build_final_prompt(db, session_id)
    try:
        text = llm.generate_text(prompt, system=prompts.FINAL_SYSTEM, max_tokens=320).strip()
    except Exception as exc:
        report_pipeline_error("final", exc)
        log.warning("final summary for session %s failed (attempt %s): %s",
                    session_id, _attempts[session_id], type(exc).__name__)
        return None
    if not text:
        return None
    save_summary(db, session_id, "final", text, model=settings.LLM_MODEL_RECAP,
                 input_text=prompt)
    db.get(m.Session, session_id).final_summary = text
    db.commit()
    _attempts.pop(session_id, None)
    return text

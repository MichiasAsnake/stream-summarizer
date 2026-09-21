"""Session routes: summary/recap/transcript/events/stream(SSE)."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m
from app.db.session import get_db
from app.llm.base import get_llm
from app.summarize.rolling import build_recap, save_summary

router = APIRouter()
_subscribers: dict[int, list[asyncio.Queue]] = {}


class SessionBus:
    """Pipeline-facing publisher scoped to exactly one session."""

    def __init__(self, session_id: int):
        self.session_id = session_id

    async def publish(self, kind: str, payload: dict) -> None:
        await publish(self.session_id, kind, payload)


def _require_session(db: SASession, sid: int) -> m.Session:
    session = db.get(m.Session, sid)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.get("/sessions")
def list_sessions(limit: int = 50, db: SASession = Depends(get_db)):
    """Most recent sessions first, for session discovery in the UI."""
    limit = max(1, min(limit, 200))
    rows = db.execute(select(m.Session, m.Channel.twitch_login)
                      .join(m.Channel, m.Channel.id == m.Session.channel_id)
                      .order_by(m.Session.id.desc()).limit(limit)).all()
    return [{"id": s.id, "channel_id": s.channel_id, "twitch_login": login,
             "source": s.source, "status": s.status, "title": s.title,
             "started_at": s.started_at, "ended_at": s.ended_at,
             "last_error": s.last_error} for s, login in rows]


@router.get("/sessions/{sid}")
def get_session(sid: int, db: SASession = Depends(get_db)):
    s = _require_session(db, sid)
    return {"id": s.id, "status": s.status, "title": s.title, "category": s.category,
            "last_error": s.last_error, "last_error_at": s.last_error_at}


@router.get("/sessions/{sid}/summary")
def get_summary(sid: int, db: SASession = Depends(get_db)):
    _require_session(db, sid)
    row = db.execute(select(m.Summary).where(m.Summary.session_id == sid,
                                             m.Summary.kind == "rolling")
                     .order_by(m.Summary.id.desc())).scalars().first()
    return {"id": row.id if row else None,
            "text": row.text if row else "",
            "covers_t_start": row.covers_t_start if row else None,
            "covers_t_end": row.covers_t_end if row else None}


def _recap_snapshot(db: SASession, sid: int) -> str:
    """Material-change snapshot: new events or thread movement only.

    Window-only churn (silence/small-talk windows closing) does not count,
    so a regen always has fresh story to tell.
    """
    ev_max = db.execute(select(func.coalesce(func.max(m.Event.id), 0))
                        .where(m.Event.session_id == sid)).scalar() or 0
    sess = db.get(m.Session, sid)
    th_max = ""
    if sess is not None:
        th_max = db.execute(select(func.coalesce(func.max(m.Thread.last_updated_at), ""))
                            .where(m.Thread.channel_id == sess.channel_id)).scalar() or ""
    return f"{ev_max}:{th_max}"


@router.get("/sessions/{sid}/recap-status")
def recap_status(sid: int, db: SASession = Depends(get_db)):
    """Cheap poll target for the NEW badge: changes only when a click would
    return a freshly regenerated recap."""
    _require_session(db, sid)
    return {"key": _recap_snapshot(db, sid)}


@router.get("/sessions/{sid}/recap")
def get_recap(sid: int, db: SASession = Depends(get_db)):
    """Cached recap (§5.10): serve stored text unless something material happened.

    'Something material' = new event or thread update since the cached recap
    (compared as a snapshot of max event id + max thread timestamp).
    Window-only churn regenerates nothing and returns instantly.
    """
    _require_session(db, sid)
    key = _recap_snapshot(db, sid)
    cached = db.execute(select(m.Summary).where(m.Summary.session_id == sid,
                                                m.Summary.kind == "recap")
                        .order_by(m.Summary.id.desc())).scalars().first()
    if cached is not None and getattr(cached, "cache_key", None) == key:
        return {"id": cached.id, "text": cached.text, "cached": True}
    from app.llm.budget import budget_status, should_skip_extraction
    month_prefix = datetime.now(UTC).strftime("%Y-%m")
    budget = budget_status(db, sid, month_prefix, settings.LLM_SESSION_BUDGET_USD,
                           settings.LLM_MONTHLY_BUDGET_USD)
    skip, reason = should_skip_extraction(budget)
    if skip:
        if cached is not None:
            return {"id": cached.id, "text": cached.text, "cached": True,
                    "budget_limited": True}
        raise HTTPException(429, reason)
    try:
        fresh, recap_prompt = build_recap(
            db, sid, get_llm("recap"), include_prompt=True)
    except Exception as exc:
        from app.observability import report_pipeline_error, set_session_error
        report_pipeline_error("recap", exc)
        set_session_error(db, sid, "recap", exc)
        db.commit()
        raise HTTPException(503, "recap generation temporarily unavailable") from exc
    from app.observability import clear_session_error
    clear_session_error(db, sid, "recap")
    row = save_summary(db, sid, "recap", fresh, model=settings.LLM_MODEL_RECAP,
                       input_text=recap_prompt)
    row.cache_key = key
    db.commit()
    return {"id": row.id, "text": fresh, "cached": False}


class FeedbackIn(BaseModel):
    summary_id: int | None = None
    kind: str = "recap"
    vote: str = "yes"


@router.post("/sessions/{sid}/feedback")
def post_feedback(sid: int, body: FeedbackIn, db: SASession = Depends(get_db)):
    """Confidence vote on the displayed summary. A No / Not Sure on a recap
    invalidates its cache so the next catch-up regenerates in simpler style."""
    _require_session(db, sid)
    if body.kind not in ("rolling", "recap"):
        raise HTTPException(400, "kind must be rolling|recap")
    if body.vote not in ("yes", "no", "not_sure"):
        raise HTTPException(400, "vote must be yes|no|not_sure")
    if body.summary_id is not None:
        summary = db.get(m.Summary, body.summary_id)
        if summary is None or summary.session_id != sid or summary.kind != body.kind:
            raise HTTPException(422, "summary does not belong to this session and kind")
    db.add(m.SummaryFeedback(summary_id=body.summary_id, session_id=sid,
                             kind=body.kind, vote=body.vote,
                             created_at=datetime.now(UTC).isoformat()))
    db.commit()
    if body.kind == "recap" and body.vote in ("no", "not_sure"):
        latest = db.execute(select(m.Summary).where(m.Summary.session_id == sid,
                                                    m.Summary.kind == "recap")
                            .order_by(m.Summary.id.desc())).scalars().first()
        if latest is not None:
            latest.cache_key = "stale-feedback"
            db.commit()
    return {"ok": True}


@router.get("/sessions/{sid}/feedback")
def get_feedback(sid: int, db: SASession = Depends(get_db)):
    _require_session(db, sid)
    counts = {"rolling": {"yes": 0, "no": 0, "not_sure": 0},
              "recap": {"yes": 0, "no": 0, "not_sure": 0}}
    rows = db.execute(select(m.SummaryFeedback).where(
        m.SummaryFeedback.session_id == sid)).scalars().all()
    for r in rows:
        if r.kind in counts and r.vote in counts[r.kind]:
            counts[r.kind][r.vote] += 1
    return {"counts": counts, "total": len(rows)}


@router.get("/sessions/{sid}/transcript")
def get_transcript(sid: int, from_: float = 0, to: float = 1e9, limit: int | None = None,
                   db: SASession = Depends(get_db)):
    """Segments in time order; limit returns only the latest N."""
    _require_session(db, sid)
    q = select(m.Segment).where(m.Segment.session_id == sid,
                                m.Segment.t_start >= from_, m.Segment.t_start <= to)
    if limit is not None:
        q = q.order_by(m.Segment.t_start.desc(), m.Segment.id.desc()).limit(max(1, limit))
        rows = list(reversed(db.execute(q).scalars().all()))
    else:
        rows = db.execute(q.order_by(m.Segment.t_start)).scalars().all()
    return [{"id": r.id, "t_start": r.t_start, "t_end": r.t_end, "text": r.text,
             "speaker_id": r.speaker_id, "speaker_conf": r.speaker_conf} for r in rows]


@router.get("/sessions/{sid}/events")
def get_events(sid: int, db: SASession = Depends(get_db)):
    _require_session(db, sid)
    rows = db.execute(select(m.Event).where(m.Event.session_id == sid)
                      .order_by(m.Event.t_start)).scalars().all()
    return [{"id": e.id, "t_start": e.t_start, "description": e.description,
             "importance": e.importance, "thread_id": e.thread_id} for e in rows]


@router.get("/sessions/{sid}/stream")
async def stream(sid: int, db: SASession = Depends(get_db)):
    _require_session(db, sid)
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.setdefault(sid, []).append(q)

    async def gen():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(msg)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            listeners = _subscribers.get(sid, [])
            if q in listeners:
                listeners.remove(q)
            if not listeners:
                _subscribers.pop(sid, None)
    return StreamingResponse(gen(), media_type="text/event-stream")


async def publish(sid: int, kind: str, payload: dict):
    message = {"type": kind, "session_id": sid, **payload}
    for q in list(_subscribers.get(sid, [])):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(message)

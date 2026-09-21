"""Session routes: summary/recap/transcript/events/stream(SSE)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db
from app.summarize.rolling import build_recap, save_summary
from app.llm.base import get_llm
from app.config import settings

router = APIRouter()
_subscribers: list[asyncio.Queue] = []


@router.get("/sessions/{sid}")
def get_session(sid: int, db: SASession = Depends(get_db)):
    s = db.get(m.Session, sid)
    return {"id": s.id, "status": s.status, "title": s.title, "category": s.category} if s else {"error": "not found"}


@router.get("/sessions/{sid}/summary")
def get_summary(sid: int, db: SASession = Depends(get_db)):
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
    return {"key": _recap_snapshot(db, sid)}


@router.get("/sessions/{sid}/recap")
def get_recap(sid: int, db: SASession = Depends(get_db)):
    """Cached recap (§5.10): serve stored text unless something material happened.

    'Something material' = new event or thread update since the cached recap
    (compared as a snapshot of max event id + max thread timestamp).
    Window-only churn regenerates nothing and returns instantly.
    """
    try:
        db.execute(text("ALTER TABLE summaries ADD COLUMN cache_key TEXT"))
        db.commit()
    except Exception:
        db.rollback()
    key = _recap_snapshot(db, sid)
    cached = db.execute(select(m.Summary).where(m.Summary.session_id == sid,
                                                m.Summary.kind == "recap")
                        .order_by(m.Summary.id.desc())).scalars().first()
    if cached is not None and getattr(cached, "cache_key", None) == key:
        return {"id": cached.id, "text": cached.text, "cached": True}
    fresh = build_recap(db, sid, get_llm("recap"))
    row = save_summary(db, sid, "recap", fresh, model=settings.LLM_MODEL_RECAP)
    try:
        db.execute(text("UPDATE summaries SET cache_key=:k WHERE id=:i"),
                   {"k": key, "i": row.id})
        db.commit()
    except Exception:
        db.rollback()
    return {"id": row.id, "text": fresh, "cached": False}


class FeedbackIn(BaseModel):
    summary_id: int | None = None
    kind: str = "recap"
    vote: str = "yes"


@router.post("/sessions/{sid}/feedback")
def post_feedback(sid: int, body: FeedbackIn, db: SASession = Depends(get_db)):
    """Confidence vote on the displayed summary. A No / Not Sure on a recap
    invalidates its cache so the next catch-up regenerates in simpler style."""
    if body.kind not in ("rolling", "recap"):
        raise HTTPException(400, "kind must be rolling|recap")
    if body.vote not in ("yes", "no", "not_sure"):
        raise HTTPException(400, "vote must be yes|no|not_sure")
    db.add(m.SummaryFeedback(summary_id=body.summary_id, session_id=sid,
                             kind=body.kind, vote=body.vote,
                             created_at=datetime.now(timezone.utc).isoformat()))
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
    counts = {"rolling": {"yes": 0, "no": 0, "not_sure": 0},
              "recap": {"yes": 0, "no": 0, "not_sure": 0}}
    rows = db.execute(select(m.SummaryFeedback).where(
        m.SummaryFeedback.session_id == sid)).scalars().all()
    for r in rows:
        if r.kind in counts and r.vote in counts[r.kind]:
            counts[r.kind][r.vote] += 1
    return {"counts": counts, "total": len(rows)}


@router.get("/sessions/{sid}/transcript")
def get_transcript(sid: int, from_: float = 0, to: float = 1e9, db: SASession = Depends(get_db)):
    rows = db.execute(select(m.Segment).where(m.Segment.session_id == sid,
                                              m.Segment.t_start >= from_, m.Segment.t_start <= to)
                      .order_by(m.Segment.t_start)).scalars().all()
    return [{"id": r.id, "t_start": r.t_start, "t_end": r.t_end, "text": r.text,
             "speaker_id": r.speaker_id, "speaker_conf": r.speaker_conf} for r in rows]


@router.get("/sessions/{sid}/events")
def get_events(sid: int, db: SASession = Depends(get_db)):
    rows = db.execute(select(m.Event).where(m.Event.session_id == sid)
                      .order_by(m.Event.t_start)).scalars().all()
    return [{"id": e.id, "t_start": e.t_start, "description": e.description,
             "importance": e.importance, "thread_id": e.thread_id} for e in rows]


@router.get("/sessions/{sid}/stream")
async def stream(sid: int):
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)

    async def gen():
        try:
            while True:
                msg = await q.get()
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            _subscribers.remove(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


async def publish(kind: str, payload: dict):
    for q in list(_subscribers):
        await q.put({"type": kind, **payload})

"""Memory + speaker correction routes (§5.12). All corrections logged to corrections table."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db

router = APIRouter()


def _log(db: SASession, ttype: str, tid: int, before: dict, after: dict):
    db.add(m.Correction(target_type=ttype, target_id=tid, before_json=json.dumps(before),
                        after_json=json.dumps(after),
                        created_at=datetime.now(timezone.utc).isoformat()))


@router.get("/channels/{cid}/entities")
def list_entities(cid: int, db: SASession = Depends(get_db)):
    return [{"id": e.id, "name": e.canonical_name, "type": e.type, "status": e.status}
            for e in db.execute(select(m.Entity).where(m.Entity.channel_id == cid)).scalars().all()]


@router.get("/channels/{cid}/threads")
def list_threads(cid: int, db: SASession = Depends(get_db)):
    return [{"id": t.id, "title": t.title, "status": t.status}
            for t in db.execute(select(m.Thread).where(m.Thread.channel_id == cid)).scalars().all()]


@router.patch("/entities/{eid}")
def patch_entity(eid: int, canonical_name: str | None = None, description: str | None = None,
                 db: SASession = Depends(get_db)):
    e = db.get(m.Entity, eid)
    before = {"name": e.canonical_name, "description": e.description}
    if canonical_name:
        e.canonical_name = canonical_name
    if description is not None:
        e.description = description
    _log(db, "entity", eid, before, {"name": e.canonical_name, "description": e.description})
    db.commit()
    return {"ok": True}


@router.post("/entities/merge")
def merge_entities(into_id: int, from_id: int, db: SASession = Depends(get_db)):
    f = db.get(m.Entity, from_id)
    before = {"status": f.status, "merged_into": f.merged_into}
    f.status = "merged"
    f.merged_into = into_id
    _log(db, "entity", from_id, before, {"status": "merged", "merged_into": into_id})
    db.commit()
    return {"ok": True}


@router.get("/channels/{cid}/merge-suggestions")
def merge_suggestions(cid: int, db: SASession = Depends(get_db)):
    """Review-only entity merge suggestions via Jev Noul adjudication.
    Applying still goes through POST /entities/merge."""
    import httpx
    from app.memory.merge import suggest_merges
    try:
        return {"suggestions": suggest_merges(db, cid)}
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(503, f"jev request failed: {type(e).__name__}")


@router.patch("/threads/{tid}")
def patch_thread(tid: int, status: str | None = None, title: str | None = None,
                 db: SASession = Depends(get_db)):
    t = db.get(m.Thread, tid)
    before = {"status": t.status, "title": t.title}
    if status:
        t.status = status
    if title:
        t.title = title
    _log(db, "thread", tid, before, {"status": t.status, "title": t.title})
    db.commit()
    return {"ok": True}

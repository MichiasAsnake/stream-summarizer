"""Channel + session routes (§5.1, §5.12)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db
from app.task_manager import TaskManager, ManagedTask

router = APIRouter()

BUS: asyncio.Queue = asyncio.Queue()


def get_db_factory(request: Request):
    return request.app.state.db_factory


@router.post("/channels")
def create_channel(twitch_login: str, content_profile: str = "", db: SASession = Depends(get_db)):
    import json
    from app.config import settings
    from app.content_profiles import validate
    profile = validate(content_profile or settings.CONTENT_PROFILE)
    now = datetime.now(timezone.utc).isoformat()
    ch = m.Channel(twitch_login=twitch_login.lower(),
                   config_json=json.dumps({"content_profile": profile}), created_at=now)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return {"id": ch.id, "twitch_login": ch.twitch_login, "content_profile": profile}


@router.get("/channels")
def list_channels(db: SASession = Depends(get_db)):
    return [{"id": c.id, "twitch_login": c.twitch_login}
            for c in db.execute(select(m.Channel)).scalars().all()]


@router.post("/channels/{cid}/monitor")
async def start_monitor(cid: int, request: Request, db: SASession = Depends(get_db)):
    """Starts the live ingest pipeline as a managed background task (Fix 1)."""
    from app.pipeline import PipelineConfig, run_session
    from app.ingest.supervisor import build_live_cmd
    tm: TaskManager = request.app.state.task_manager
    rows = db.execute(
        select(m.Session)
        .where(m.Session.channel_id == cid, m.Session.source == "live")
        .order_by(m.Session.id.desc()).limit(1)).scalars().all()
    if rows and tm.has_running(rows[0].id):
        return {"session_id": rows[0].id, "status": "already-running"}
    if not rows:
        now = datetime.now(timezone.utc).isoformat()
        s = m.Session(channel_id=cid, source="live", status="live", started_at=now)
        db.add(s); db.commit(); db.refresh(s)
        sid = s.id
    else:
        sid = rows[0].id
    ch = db.get(m.Channel, cid)
    login = ch.twitch_login if ch else "xqc"
    cfg = PipelineConfig(session_id=sid, channel_id=cid, cmd=build_live_cmd(login))
    db_factory = request.app.state.db_factory
    t = asyncio.create_task(run_session(cfg, db_factory=db_factory))
    tm.set(sid, ManagedTask(task=t, cfg=cfg))
    return {"session_id": sid, "status": "pipeline-started"}


@router.delete("/channels/{cid}/monitor")
async def stop_monitor(cid: int, request: Request, db: SASession = Depends(get_db)):
    """Stops the managed pipeline task for this channel's latest session."""
    from app.task_manager import TaskManager
    tm: TaskManager = request.app.state.task_manager
    rows = db.execute(
        select(m.Session)
        .where(m.Session.channel_id == cid, m.Session.source == "live")
        .order_by(m.Session.id.desc()).limit(1)).scalars().all()
    if rows:
        tm.stop(rows[0].id)
    return {"ok": True}


@router.post("/channels/{cid}/replay")
async def start_replay(cid: int, url: str = "", file: str = "",
                         request: Request = Depends(),
                         db: SASession = Depends(get_db)):
    """Launch replay pipeline as a managed task (Fix 1)."""
    from app.pipeline import PipelineConfig, run_session
    from app.ingest.supervisor import read_pcm_file
    from app.db import models as _m
    tm: TaskManager = request.app.state.task_manager
    now = datetime.now(timezone.utc).isoformat()
    s = _m.Session(channel_id=cid, source="replay", status="live",
                   title=url or file, started_at=now)
    db.add(s); db.commit(); db.refresh(s)
    sid = s.id
    source = url or file
    cfg = PipelineConfig(session_id=sid, channel_id=cid, cmd=source)
    db_factory = request.app.state.db_factory
    t = asyncio.create_task(run_session(cfg, db_factory=db_factory))
    tm.set(sid, ManagedTask(task=t, cfg=cfg))
    return {"session_id": sid, "status": "replay-started"}
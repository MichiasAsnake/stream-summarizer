"""Channel + session routes (§5.1, §5.12)."""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db
from app.task_manager import ManagedTask, TaskManager

router = APIRouter()

TWITCH_LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{1,25}$")


def get_db_factory(request: Request):
    return request.app.state.db_factory


@router.post("/channels")
def create_channel(twitch_login: str, content_profile: str = "", db: SASession = Depends(get_db)):
    import json

    from app.config import settings
    from app.content_profiles import validate
    twitch_login = twitch_login.strip()
    if not TWITCH_LOGIN_RE.fullmatch(twitch_login):
        raise HTTPException(422, "invalid Twitch login")
    profile = validate(content_profile or settings.CONTENT_PROFILE)
    now = datetime.now(UTC).isoformat()
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
    from app.ingest.supervisor import build_live_cmd
    from app.pipeline import PipelineConfig, run_session
    tm: TaskManager = request.app.state.task_manager
    rows = db.execute(
        select(m.Session)
        .where(m.Session.channel_id == cid, m.Session.source == "live")
        .order_by(m.Session.id.desc()).limit(1)).scalars().all()
    if rows and tm.has_running(rows[0].id):
        return {"session_id": rows[0].id, "status": "already-running"}
    ch = db.get(m.Channel, cid)
    if ch is None:
        raise HTTPException(404, "channel not found")
    now = datetime.now(UTC).isoformat()
    s = m.Session(channel_id=cid, source="live", status="live", started_at=now)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    cfg = PipelineConfig(session_id=sid, channel_id=cid,
                         cmd=build_live_cmd(ch.twitch_login), reconnect=True)
    db_factory = request.app.state.db_factory
    from app.api.routes_sessions import SessionBus
    t = asyncio.create_task(run_session(
        cfg, db_factory=db_factory, bus=SessionBus(sid)))
    tm.set(sid, ManagedTask(task=t, cfg=cfg))
    return {"session_id": sid, "status": "pipeline-started"}


@router.delete("/channels/{cid}/monitor")
async def stop_monitor(cid: int, request: Request, db: SASession = Depends(get_db)):
    """Stops the managed pipeline task for this channel's latest session."""
    tm: TaskManager = request.app.state.task_manager
    rows = db.execute(
        select(m.Session)
        .where(m.Session.channel_id == cid, m.Session.source == "live")
        .order_by(m.Session.id.desc()).limit(1)).scalars().all()
    if rows:
        await tm.stop(rows[0].id)
    return {"ok": True}


@router.post("/channels/{cid}/replay")
async def start_replay(cid: int, request: Request, url: str = "", file: str = "",
                         db: SASession = Depends(get_db)):
    """Launch replay pipeline as a managed task (Fix 1)."""
    from app.db import models as _m
    from app.ingest.supervisor import build_replay_cmd
    from app.pipeline import PipelineConfig, run_session
    if db.get(_m.Channel, cid) is None:
        raise HTTPException(404, "channel not found")
    if bool(url) == bool(file):
        raise HTTPException(422, "provide exactly one of url or file")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise HTTPException(422, "replay URL must be an HTTP(S) URL without credentials")
        source = url
    else:
        try:
            replay_path = Path(file).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise HTTPException(404, "replay file not found") from None
        if not replay_path.is_file():
            raise HTTPException(422, "replay source is not a file")
        source = str(replay_path)
    tm: TaskManager = request.app.state.task_manager
    now = datetime.now(UTC).isoformat()
    s = _m.Session(channel_id=cid, source="replay", status="live",
                   title=url or file, started_at=now)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    cfg = PipelineConfig(session_id=sid, channel_id=cid,
                         cmd=build_replay_cmd(source), reconnect=False)
    db_factory = request.app.state.db_factory
    from app.api.routes_sessions import SessionBus
    t = asyncio.create_task(run_session(
        cfg, db_factory=db_factory, bus=SessionBus(sid)))
    tm.set(sid, ManagedTask(task=t, cfg=cfg))
    return {"session_id": sid, "status": "replay-started"}

"""Channel + session routes (§5.1, §5.12)."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
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
def create_channel(twitch_login: str, content_profile: str = "", auto_monitor: bool = False,
                   db: SASession = Depends(get_db)):
    from app.config import settings
    from app.content_profiles import validate
    twitch_login = twitch_login.strip()
    if not TWITCH_LOGIN_RE.fullmatch(twitch_login):
        raise HTTPException(422, "invalid Twitch login")
    profile = validate(content_profile or settings.CONTENT_PROFILE)
    now = datetime.now(UTC).isoformat()
    ch = m.Channel(twitch_login=twitch_login.lower(),
                   config_json=json.dumps({"content_profile": profile,
                                           "auto_monitor": auto_monitor}),
                   created_at=now)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return {"id": ch.id, "twitch_login": ch.twitch_login, "content_profile": profile,
            "auto_monitor": auto_monitor}


@router.get("/channels")
def list_channels(db: SASession = Depends(get_db)):
    return [{"id": c.id, "twitch_login": c.twitch_login,
             "auto_monitor": bool(channel_config(c).get("auto_monitor"))}
            for c in db.execute(select(m.Channel)).scalars().all()]


def _launch(app, cfg, key: str) -> None:
    """Start run_session under a lease already acquired for key."""
    from app.api.routes_sessions import SessionBus
    from app.leases import run_leased
    from app.pipeline import run_session
    tm: TaskManager = app.state.task_manager
    db_factory = app.state.db_factory
    t = asyncio.create_task(run_leased(
        lambda: run_session(cfg, db_factory=db_factory, bus=SessionBus(cfg.session_id)),
        key=key, owner=app.state.worker_id, db_factory=db_factory))
    tm.set(cfg.session_id, ManagedTask(task=t, cfg=cfg))


def start_live_session(app, db: SASession, ch: m.Channel, info=None) -> dict:
    """Start a channel's live pipeline. A per-channel database lease
    guarantees one monitor per channel across requests, workers and the
    auto-monitor. info (StreamInfo) seeds the session's stream metadata."""
    from app.ingest.supervisor import build_live_cmd
    from app.leases import (
        attach_session,
        current,
        live_key,
        reconcile_orphans,
        release,
        try_acquire,
    )
    from app.pipeline import PipelineConfig
    key, owner = live_key(ch.id), app.state.worker_id
    if not try_acquire(db, key, owner):
        holder = current(db, key)
        return {"session_id": holder.session_id if holder else None,
                "status": "already-running"}
    try:
        # We own the channel now, so any older live session left "live" by a
        # crashed worker is definitively orphaned.
        reconcile_orphans(db, channel_id=ch.id, source="live", grace_seconds=0)
        now = datetime.now(UTC).isoformat()
        s = m.Session(channel_id=ch.id, source="live", status="live", started_at=now)
        if info is not None:
            s.twitch_stream_id, s.title, s.category = info.stream_id, info.title, info.category
            if info.title or info.category:
                s.meta_history = json.dumps(
                    [{"at": now, "title": info.title, "category": info.category}])
        db.add(s)
        db.commit()
        db.refresh(s)
        attach_session(db, key, owner, s.id)
        cfg = PipelineConfig(session_id=s.id, channel_id=ch.id,
                             cmd=build_live_cmd(ch.twitch_login), reconnect=True)
        _launch(app, cfg, key)
    except Exception:
        release(db, key, owner)
        raise
    return {"session_id": s.id, "status": "pipeline-started"}


@router.post("/channels/{cid}/monitor")
async def start_monitor(cid: int, request: Request, db: SASession = Depends(get_db)):
    """Start the live pipeline now (see also PUT /channels/{cid}/auto-monitor)."""
    ch = db.get(m.Channel, cid)
    if ch is None:
        raise HTTPException(404, "channel not found")
    return start_live_session(request.app, db, ch)


class AutoMonitorIn(BaseModel):
    enabled: bool


def channel_config(ch: m.Channel) -> dict:
    try:
        return json.loads(ch.config_json or "{}")
    except json.JSONDecodeError:
        return {}


@router.put("/channels/{cid}/auto-monitor")
def set_auto_monitor(cid: int, body: AutoMonitorIn, db: SASession = Depends(get_db)):
    """Opt a channel in or out of starting a monitor automatically when it goes live."""
    ch = db.get(m.Channel, cid)
    if ch is None:
        raise HTTPException(404, "channel not found")
    cfg = channel_config(ch)
    cfg["auto_monitor"] = body.enabled
    ch.config_json = json.dumps(cfg)
    db.commit()
    return {"id": ch.id, "auto_monitor": body.enabled}


@router.delete("/channels/{cid}/monitor")
async def stop_monitor(cid: int, request: Request, db: SASession = Depends(get_db)):
    """Stop this channel's live pipeline, whichever worker is running it."""
    from app.leases import current, live_key, request_stop
    tm: TaskManager = request.app.state.task_manager
    key = live_key(cid)
    holder = current(db, key)
    if holder is None:
        return {"ok": True, "status": "not-running"}
    if holder.owner == request.app.state.worker_id and holder.session_id is not None:
        await tm.stop(holder.session_id)
        return {"ok": True, "status": "stopped", "session_id": holder.session_id}
    request_stop(db, key)
    return {"ok": True, "status": "stop-requested", "session_id": holder.session_id}


@router.post("/channels/{cid}/replay")
async def start_replay(cid: int, request: Request, url: str = "", file: str = "",
                         db: SASession = Depends(get_db)):
    """Launch replay pipeline as a managed task (Fix 1)."""
    from app.db import models as _m
    from app.ingest.supervisor import build_replay_cmd
    from app.pipeline import PipelineConfig
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
    from app.leases import release, session_key, try_acquire
    now = datetime.now(UTC).isoformat()
    s = _m.Session(channel_id=cid, source="replay", status="live",
                   title=url or file, started_at=now)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    key, owner = session_key(sid), request.app.state.worker_id
    if not try_acquire(db, key, owner, session_id=sid):
        raise HTTPException(409, "replay session is already owned")
    try:
        cfg = PipelineConfig(session_id=sid, channel_id=cid,
                             cmd=build_replay_cmd(source), reconnect=False)
        _launch(request.app, cfg, key)
    except Exception:
        release(db, key, owner)
        raise
    return {"session_id": sid, "status": "replay-started"}

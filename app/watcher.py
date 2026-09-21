"""Auto-monitor: start monitors when opted-in channels go live, keep stream
metadata current, and write final summaries for finished sessions.

Only one worker polls at a time (it holds the "watcher" lease); starting a
monitor still goes through the per-channel lease, so the watcher and manual
starts can never create duplicates.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m
from app.ingest.live_status import LiveChecker, StreamInfo

log = logging.getLogger(__name__)

WATCHER_KEY = "watcher"


def _auto_channels(db: SASession) -> list[m.Channel]:
    from app.api.routes_channels import channel_config
    return [c for c in db.execute(select(m.Channel)).scalars()
            if channel_config(c).get("auto_monitor")]


def _in_cooldown(db: SASession, channel_id: int, now: datetime) -> bool:
    """Last live session ended recently without capturing any speech."""
    last = db.execute(select(m.Session).where(
        m.Session.channel_id == channel_id, m.Session.source == "live")
        .order_by(m.Session.id.desc()).limit(1)).scalars().first()
    if last is None or not last.ended_at:
        return False
    cutoff = now - timedelta(minutes=settings.AUTO_MONITOR_RESTART_COOLDOWN_MINUTES)
    if datetime.fromisoformat(last.ended_at) < cutoff:
        return False
    return not db.execute(select(exists().where(m.Segment.session_id == last.id))).scalar()


def update_metadata(db: SASession, session_id: int, info: StreamInfo, now: datetime) -> bool:
    """Record title/category changes on the running session."""
    s = db.get(m.Session, session_id)
    if s is None:
        return False
    changed = False
    if info.stream_id and not s.twitch_stream_id:
        s.twitch_stream_id = info.stream_id
        changed = True
    new_title = info.title or s.title
    new_category = info.category or s.category
    if (new_title, new_category) != (s.title, s.category):
        history = json.loads(s.meta_history or "[]")
        history.append({"at": now.isoformat(), "title": new_title, "category": new_category})
        s.title, s.category, s.meta_history = new_title, new_category, json.dumps(history)
        changed = True
    if changed:
        db.commit()
    return changed


def _hold_watcher_lease(db: SASession, owner: str) -> bool:
    from app.leases import renew, try_acquire
    ttl = settings.AUTO_MONITOR_POLL_SECONDS * 3
    held, _ = renew(db, WATCHER_KEY, owner, ttl)
    return held or try_acquire(db, WATCHER_KEY, owner, ttl=ttl)


def _finalize_one(db_factory, session_id: int) -> None:
    from app.finalize import finalize_session
    from app.llm.base import get_llm
    db = db_factory()
    try:
        finalize_session(db, session_id, get_llm("recap"))
    finally:
        db.close()


async def watch_tick(app, checker: LiveChecker | None, now: datetime | None = None) -> dict:
    """One poll. Returns what happened (for logs and tests)."""
    from app.api.routes_channels import start_live_session
    from app.finalize import pending_sessions
    from app.leases import current, live_key
    now = now or datetime.now(UTC)
    result: dict = {"started": [], "updated": [], "cooldown": [], "finalized": []}
    db = app.state.db_factory()
    try:
        if not _hold_watcher_lease(db, app.state.worker_id):
            return {"skipped": "another worker is watching"}
        channels = _auto_channels(db) if settings.AUTO_MONITOR_ENABLED else []
        live: dict[str, StreamInfo] = {}
        if channels and checker is not None:
            try:
                live = await checker.check([c.twitch_login for c in channels])
            except Exception as exc:  # unknown is not offline: change nothing
                log.warning("live check via %s failed: %s", checker.name, exc)
                result["error"] = f"{type(exc).__name__}: {exc}"
        for ch in channels:
            info = live.get(ch.twitch_login)
            if info is None:
                continue  # offline: a running monitor ends itself after the audio stops
            holder = current(db, live_key(ch.id))
            if holder is None:
                if _in_cooldown(db, ch.id, now):
                    result["cooldown"].append(ch.id)
                    continue
                started = start_live_session(app, db, ch, info)
                if started["status"] == "pipeline-started":
                    log.info("auto-monitor: %s is live, started session %s",
                             ch.twitch_login, started["session_id"])
                    result["started"].append(started["session_id"])
            elif holder.session_id and update_metadata(db, holder.session_id, info, now):
                result["updated"].append(holder.session_id)
        if settings.FINAL_SUMMARY_ENABLED:
            for sid in pending_sessions(db):
                await asyncio.to_thread(_finalize_one, app.state.db_factory, sid)
                if db.get(m.Session, sid, populate_existing=True).final_summary:
                    result["finalized"].append(sid)
    finally:
        db.close()
    return result


async def watch_loop(app) -> None:
    from app.ingest.live_status import make_checker
    checker = make_checker()
    while True:
        try:
            await watch_tick(app, checker)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto-monitor tick failed")
        await asyncio.sleep(settings.AUTO_MONITOR_POLL_SECONDS)

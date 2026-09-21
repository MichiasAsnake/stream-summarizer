"""Database-backed pipeline leases and crash recovery.

TaskManager only knows about tasks in this process. Leases make ownership
visible to every worker sharing the database:

- a live monitor holds "live:{channel_id}", so two requests (or two uvicorn
  workers) cannot start duplicate monitors for one channel;
- a replay holds "session:{session_id}";
- the owner renews its lease every TTL/3; a crashed worker's lease simply
  expires, and reconcile_orphans() then closes its sessions as "interrupted";
- a stop request for a pipeline owned by another worker is recorded on the
  lease and honoured by the owner at its next heartbeat.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m

log = logging.getLogger(__name__)

RUNNING_STATUSES = ("starting", "live", "degraded")


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def live_key(channel_id: int) -> str:
    return f"live:{channel_id}"


def session_key(session_id: int) -> str:
    return f"session:{session_id}"


def _ttl(ttl: float | None) -> float:
    return settings.MONITOR_LEASE_TTL_SECONDS if ttl is None else ttl


def try_acquire(db: SASession, key: str, owner: str, session_id: int | None = None,
                ttl: float | None = None, now: float | None = None) -> bool:
    """Atomically take the lease if it is free or expired.

    A worker cannot re-acquire a lease it already holds, which also blocks
    duplicate tasks inside a single process.
    """
    now = time.time() if now is None else now
    expires = now + _ttl(ttl)
    try:
        db.add(m.MonitorLease(key=key, session_id=session_id, owner=owner,
                              expires_at=expires, stop_requested=0))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
    res = db.execute(
        update(m.MonitorLease)
        .where(m.MonitorLease.key == key, m.MonitorLease.expires_at < now)
        .values(owner=owner, session_id=session_id, expires_at=expires, stop_requested=0))
    db.commit()
    return res.rowcount == 1


def attach_session(db: SASession, key: str, owner: str, session_id: int) -> None:
    db.execute(update(m.MonitorLease)
               .where(m.MonitorLease.key == key, m.MonitorLease.owner == owner)
               .values(session_id=session_id))
    db.commit()


def current(db: SASession, key: str, now: float | None = None) -> m.MonitorLease | None:
    """The lease if it is still valid, else None."""
    now = time.time() if now is None else now
    row = db.get(m.MonitorLease, key, populate_existing=True)
    return row if row is not None and row.expires_at >= now else None


def renew(db: SASession, key: str, owner: str, ttl: float | None = None,
          now: float | None = None) -> tuple[bool, bool]:
    """Extend our lease. Returns (still_held, stop_requested)."""
    now = time.time() if now is None else now
    res = db.execute(update(m.MonitorLease)
                     .where(m.MonitorLease.key == key, m.MonitorLease.owner == owner)
                     .values(expires_at=now + _ttl(ttl)))
    db.commit()
    if res.rowcount != 1:
        return False, False
    stop = db.execute(select(m.MonitorLease.stop_requested)
                      .where(m.MonitorLease.key == key)).scalar()
    return True, bool(stop)


def release(db: SASession, key: str, owner: str) -> None:
    db.execute(delete(m.MonitorLease)
               .where(m.MonitorLease.key == key, m.MonitorLease.owner == owner))
    db.commit()


def request_stop(db: SASession, key: str) -> bool:
    res = db.execute(update(m.MonitorLease).where(m.MonitorLease.key == key)
                     .values(stop_requested=1))
    db.commit()
    return res.rowcount == 1


def reconcile_orphans(db: SASession, *, channel_id: int | None = None,
                      source: str | None = None, grace_seconds: float | None = None,
                      now: float | None = None) -> list[int]:
    """Close sessions still marked running that no valid lease covers.

    grace_seconds skips sessions created very recently, whose owner may not
    have attached the session to its lease yet (defaults to the lease TTL).
    """
    now = time.time() if now is None else now
    grace = _ttl(None) if grace_seconds is None else grace_seconds
    leased = set(db.execute(select(m.MonitorLease.session_id)
                            .where(m.MonitorLease.expires_at >= now,
                                   m.MonitorLease.session_id.is_not(None))).scalars())
    q = select(m.Session).where(m.Session.status.in_(RUNNING_STATUSES))
    if channel_id is not None:
        q = q.where(m.Session.channel_id == channel_id)
    if source is not None:
        q = q.where(m.Session.source == source)
    cutoff = datetime.fromtimestamp(now - grace, UTC).isoformat()
    closed: list[int] = []
    stamp = datetime.fromtimestamp(now, UTC).isoformat()
    for s in db.execute(q).scalars():
        if s.id in leased or (grace > 0 and (s.started_at or "") > cutoff):
            continue
        s.status = "interrupted"
        s.ended_at = s.ended_at or stamp
        s.last_error = "session: worker stopped without closing this session"
        s.last_error_at = stamp
        closed.append(s.id)
    # Expired leases carry no information once their sessions are closed.
    db.execute(delete(m.MonitorLease).where(m.MonitorLease.expires_at < now))
    db.commit()
    if closed:
        log.warning("Marked orphaned sessions interrupted: %s", closed)
    return closed


async def run_leased(start: Callable[[], Awaitable[None]], *, key: str, owner: str,
                     db_factory, ttl: float | None = None) -> None:
    """Run a pipeline while renewing its lease; stop it if the lease is lost
    or another worker requests a stop. Always releases the lease."""
    ttl = _ttl(ttl)
    pipeline = asyncio.ensure_future(start())
    try:
        while True:
            done, _ = await asyncio.wait({pipeline}, timeout=ttl / 3)
            if done:
                return pipeline.result()
            db = db_factory()
            try:
                held, stop = renew(db, key, owner, ttl)
            except Exception:  # transient DB error: keep running, retry next beat
                log.exception("lease renewal failed for %s", key)
                continue
            finally:
                db.close()
            if not held or stop:
                log.warning("stopping %s: %s", key, "stop requested" if stop else "lease lost")
                pipeline.cancel()
                try:
                    await pipeline
                except asyncio.CancelledError:
                    pass
                return None
    finally:
        if not pipeline.done():
            pipeline.cancel()
            try:
                await pipeline
            except (asyncio.CancelledError, Exception):
                pass
        db = db_factory()
        try:
            release(db, key, owner)
        finally:
            db.close()

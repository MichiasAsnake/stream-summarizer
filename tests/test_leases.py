import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import leases
from app.api import routes_channels, routes_sessions
from app.config import settings
from app.db import models as m
from app.db.models import Base
from app.db.session import get_db
from app.task_manager import TaskManager


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'leases.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True)


def _channel(db, login="streamer"):
    ch = m.Channel(twitch_login=login, created_at=datetime.now(UTC).isoformat())
    db.add(ch)
    db.commit()
    return ch


def _session(db, cid, status="live", source="live", started=None):
    started = started or (datetime.now(UTC) - timedelta(hours=1))
    s = m.Session(channel_id=cid, source=source, status=status, started_at=started.isoformat())
    db.add(s)
    db.commit()
    return s


def test_lease_is_exclusive_until_it_expires(factory):
    db = factory()
    now = 1000.0
    assert leases.try_acquire(db, "live:1", "a", ttl=30, now=now)
    assert not leases.try_acquire(db, "live:1", "b", ttl=30, now=now + 5)
    # The holder cannot start a second copy of itself either.
    assert not leases.try_acquire(db, "live:1", "a", ttl=30, now=now + 5)
    assert leases.try_acquire(db, "live:1", "b", ttl=30, now=now + 31)
    assert leases.renew(db, "live:1", "a", ttl=30, now=now + 32) == (False, False)
    assert leases.renew(db, "live:1", "b", ttl=30, now=now + 32) == (True, False)
    assert leases.request_stop(db, "live:1")
    assert leases.renew(db, "live:1", "b", ttl=30, now=now + 33) == (True, True)
    leases.release(db, "live:1", "b")
    assert leases.current(db, "live:1") is None


def test_reconcile_closes_only_unleased_sessions(factory):
    db = factory()
    cid = _channel(db).id
    orphan = _session(db, cid).id
    owned = _session(db, cid).id
    fresh = _session(db, cid, started=datetime.now(UTC)).id
    done = _session(db, cid, status="ended").id
    assert leases.try_acquire(db, leases.live_key(cid), "worker", session_id=owned)

    assert leases.reconcile_orphans(db) == [orphan]
    statuses = {s.id: s.status for s in db.query(m.Session)}
    assert statuses == {orphan: "interrupted", owned: "live", fresh: "live", done: "ended"}
    assert db.get(m.Session, orphan).last_error.startswith("session:")
    assert db.get(m.Session, orphan).ended_at


def test_reconcile_removes_expired_leases(factory):
    db = factory()
    cid = _channel(db).id
    sid = _session(db, cid).id
    assert leases.try_acquire(db, "live:1", "dead", session_id=sid, ttl=1, now=time.time() - 10)
    assert leases.reconcile_orphans(db) == [sid]
    assert db.get(m.MonitorLease, "live:1") is None


async def test_run_leased_honours_remote_stop_and_releases(factory):
    db = factory()
    assert leases.try_acquire(db, "live:1", "owner", ttl=0.15)
    cancelled = asyncio.Event()

    async def pipeline():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(leases.run_leased(
        pipeline, key="live:1", owner="owner", db_factory=factory, ttl=0.15))
    await asyncio.sleep(0.12)
    assert leases.current(db, "live:1") is not None  # renewed past original TTL
    leases.request_stop(factory(), "live:1")
    await asyncio.wait_for(task, 2)
    assert cancelled.is_set()
    assert db.get(m.MonitorLease, "live:1", populate_existing=True) is None


async def test_run_leased_releases_after_normal_completion(factory):
    db = factory()
    assert leases.try_acquire(db, "session:7", "owner")

    async def pipeline():
        await asyncio.sleep(0.01)

    await leases.run_leased(pipeline, key="session:7", owner="owner",
                            db_factory=factory, ttl=5)
    assert db.get(m.MonitorLease, "session:7", populate_existing=True) is None


def _worker_app(factory, worker_id):
    app = FastAPI()
    app.include_router(routes_channels.router, prefix="/api/v1")
    app.include_router(routes_sessions.router, prefix="/api/v1")
    app.state.db_factory = factory
    app.state.task_manager = TaskManager()
    app.state.worker_id = worker_id

    def _db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db
    return app


def test_two_workers_share_one_monitor_and_cross_worker_stop(factory, monkeypatch):
    monkeypatch.setattr(settings, "MONITOR_LEASE_TTL_SECONDS", 0.3)
    runs = []

    async def fake_run_session(cfg, db_factory, bus=None):
        runs.append(cfg.session_id)
        await asyncio.sleep(30)
    monkeypatch.setattr("app.pipeline.run_session", fake_run_session)

    db = factory()
    cid = _channel(db).id
    stale = _session(db, cid).id  # left "live" by a crashed worker

    with TestClient(_worker_app(factory, "A")) as a, TestClient(_worker_app(factory, "B")) as b:
        first = a.post(f"/api/v1/channels/{cid}/monitor").json()
        assert first["status"] == "pipeline-started"
        second = b.post(f"/api/v1/channels/{cid}/monitor").json()
        assert second == {"session_id": first["session_id"], "status": "already-running"}
        again = a.post(f"/api/v1/channels/{cid}/monitor").json()
        assert again["status"] == "already-running"
        assert db.get(m.Session, stale, populate_existing=True).status == "interrupted"

        listed = a.get("/api/v1/sessions").json()
        assert [s["id"] for s in listed][:2] == [first["session_id"], stale]
        assert listed[0]["twitch_login"] == "streamer"

        stop = b.delete(f"/api/v1/channels/{cid}/monitor").json()
        assert stop["status"] == "stop-requested"
        deadline = time.time() + 3
        while time.time() < deadline and leases.current(db, leases.live_key(cid)) is not None:
            time.sleep(0.05)
        assert leases.current(db, leases.live_key(cid)) is None

        # Channel is free again and a new monitor can start.
        assert a.post(f"/api/v1/channels/{cid}/monitor").json()["status"] == "pipeline-started"
        assert a.delete(f"/api/v1/channels/{cid}/monitor").json()["status"] == "stopped"
    assert len(runs) == 2

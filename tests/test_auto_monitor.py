import asyncio
import json
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import finalize, leases, watcher
from app.api import routes_channels
from app.config import settings
from app.db import models as m
from app.db.models import Base
from app.db.session import get_db
from app.ingest.live_status import HelixChecker, StreamInfo, StreamlinkChecker
from app.task_manager import TaskManager


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'auto.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True)


@pytest.fixture
def fake_pipeline(monkeypatch):
    runs = []

    async def fake_run_session(cfg, db_factory, bus=None):
        runs.append(cfg.session_id)
        await asyncio.sleep(30)
    monkeypatch.setattr("app.pipeline.run_session", fake_run_session)
    return runs


def _app(factory, worker_id="w1"):
    return SimpleNamespace(state=SimpleNamespace(
        db_factory=factory, task_manager=TaskManager(), worker_id=worker_id))


def _channel(db, login="streamer", auto=True):
    ch = m.Channel(twitch_login=login, created_at=datetime.now(UTC).isoformat(),
                   config_json=json.dumps({"auto_monitor": auto}))
    db.add(ch)
    db.commit()
    return ch


class FakeChecker:
    name = "fake"

    def __init__(self, live=None, error=None):
        self.live, self.error, self.calls = live or {}, error, []

    async def check(self, logins):
        self.calls.append(logins)
        if self.error:
            raise self.error
        return {k: v for k, v in self.live.items() if k in logins}


async def test_goes_live_starts_one_monitor_and_tracks_metadata(factory, fake_pipeline):
    app = _app(factory)
    db = factory()
    ch = _channel(db)
    _channel(db, "not_opted_in", auto=False)
    checker = FakeChecker({"streamer": StreamInfo("s1", "Day 1", "GTA V"),
                           "not_opted_in": StreamInfo()})
    try:
        first = await watcher.watch_tick(app, checker)
        assert len(first["started"]) == 1
        assert checker.calls == [["streamer"]]
        sid = first["started"][0]
        s = db.get(m.Session, sid)
        assert (s.twitch_stream_id, s.title, s.category) == ("s1", "Day 1", "GTA V")

        # Still live: no duplicate; a title change is recorded.
        checker.live["streamer"] = StreamInfo("s1", "Day 1 - heist", "GTA V")
        second = await watcher.watch_tick(app, checker)
        assert second["started"] == [] and second["updated"] == [sid]
        s = db.get(m.Session, sid, populate_existing=True)
        assert s.title == "Day 1 - heist"
        assert [h["title"] for h in json.loads(s.meta_history)] == ["Day 1", "Day 1 - heist"]
        await asyncio.sleep(0.01)  # let the launched pipeline task start
        assert fake_pipeline == [sid]
        assert leases.current(db, leases.live_key(ch.id)).session_id == sid
    finally:
        await app.state.task_manager.stop_all()


async def test_offline_or_failed_check_starts_nothing(factory, fake_pipeline):
    app = _app(factory)
    db = factory()
    _channel(db)
    assert (await watcher.watch_tick(app, FakeChecker({})))["started"] == []
    failed = await watcher.watch_tick(app, FakeChecker(error=RuntimeError("twitch down")))
    assert failed["started"] == [] and "twitch down" in failed["error"]
    assert await watcher.watch_tick(app, None) == {
        "started": [], "updated": [], "cooldown": [], "finalized": [], "beats": 0}
    assert fake_pipeline == []


async def test_no_restart_loop_after_session_captured_nothing(factory, fake_pipeline):
    app = _app(factory)
    db = factory()
    ch = _channel(db)
    ended = datetime.now(UTC) - timedelta(minutes=2)
    db.add(m.Session(channel_id=ch.id, source="live", status="ended",
                     started_at=(ended - timedelta(minutes=6)).isoformat(),
                     ended_at=ended.isoformat()))
    db.commit()
    checker = FakeChecker({"streamer": StreamInfo()})
    result = await watcher.watch_tick(app, checker)
    assert result["cooldown"] == [ch.id] and result["started"] == []
    later = datetime.now(UTC) + timedelta(minutes=settings.AUTO_MONITOR_RESTART_COOLDOWN_MINUTES)
    try:
        assert len((await watcher.watch_tick(app, checker, now=later))["started"]) == 1
    finally:
        await app.state.task_manager.stop_all()


async def test_only_one_worker_polls(factory, fake_pipeline):
    db = factory()
    _channel(db)
    a, b = _app(factory, "a"), _app(factory, "b")
    checker = FakeChecker({})
    await watcher.watch_tick(a, checker)
    assert (await watcher.watch_tick(b, checker)) == {"skipped": "another worker is watching"}
    assert len(checker.calls) == 1


class FakeLLM:
    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def generate_text(self, prompt, system="", max_tokens=500):
        self.prompts.append((prompt, system))
        if self.fail:
            raise TimeoutError("provider timeout")
        return "gg stream 🔥 Morgan pulled off the heist."


def _finished_session(db, cid, with_event=True, status="ended"):
    now = datetime.now(UTC).isoformat()
    s = m.Session(channel_id=cid, source="live", status=status, title="Day 1",
                  category="GTA V", started_at=now, ended_at=now)
    db.add(s)
    db.commit()
    if with_event:
        db.add(m.Event(session_id=s.id, t_start=65.0, type="plot",
                       description="Morgan pulls off the heist", importance=4))
        db.commit()
    return s.id


def test_final_summary_is_written_once_and_retried_on_failure(factory, monkeypatch):
    monkeypatch.setattr(finalize, "_attempts", {})
    db = factory()
    cid = _channel(db).id
    sid = _finished_session(db, cid)
    empty = _finished_session(db, cid, with_event=False)
    crashed = _finished_session(db, cid, status="interrupted")
    assert set(finalize.pending_sessions(db, limit=5)) == {sid, crashed}
    assert empty not in finalize.pending_sessions(db, limit=5)

    failing = FakeLLM(fail=True)
    for _ in range(finalize.MAX_ATTEMPTS):
        assert finalize.finalize_session(db, sid, failing) is None
    assert sid not in finalize.pending_sessions(db, limit=5)  # gave up after max attempts

    llm = FakeLLM()
    assert finalize.finalize_session(db, crashed, llm).startswith("gg stream")
    prompt, system = llm.prompts[0]
    assert "Stream title: Day 1" in prompt and "0:01:05" in prompt
    assert "Morgan pulls off the heist" in prompt and "wrap-up" in system
    assert db.get(m.Session, crashed, populate_existing=True).final_summary.startswith("gg")
    assert db.query(m.Summary).filter_by(session_id=crashed, kind="final").count() == 1
    assert crashed not in finalize.pending_sessions(db, limit=5)


async def test_watch_tick_finalizes_finished_sessions(factory, monkeypatch):
    monkeypatch.setattr(finalize, "_attempts", {})
    monkeypatch.setattr("app.llm.base.get_llm", lambda kind="extract": FakeLLM())
    db = factory()
    sid = _finished_session(db, _channel(db).id)
    result = await watcher.watch_tick(_app(factory), FakeChecker({}))
    assert result["finalized"] == [sid]


def _write_fake_streamlink(tmp_path):
    script = tmp_path / "streamlink"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        login = sys.argv[-1].rsplit("/", 1)[-1]
        if login == "live_one":
            print(json.dumps({{"streams": {{"audio_only": {{}}}},
                              "metadata": {{"id": "42", "title": "Heist", "category": "GTA V"}}}}))
        elif login == "broken":
            print(json.dumps({{"error": "Unable to open URL"}})); sys.exit(1)
        else:
            print(json.dumps({{"error": "No playable streams found on this URL"}})); sys.exit(1)
        """))
    script.chmod(0o755)
    return str(script)


async def test_streamlink_checker_reads_live_offline_and_errors(tmp_path):
    checker = StreamlinkChecker(_write_fake_streamlink(tmp_path))
    assert await checker.check(["live_one", "sleepy"]) == {
        "live_one": StreamInfo("42", "Heist", "GTA V")}
    with pytest.raises(RuntimeError, match="Unable to open URL"):
        await checker.check(["broken"])


async def test_helix_checker_maps_stream_fields():
    class FakeHelix:
        async def get_streams(self, logins):
            return {"streamer": {"id": "9", "title": "Day 2", "game_name": "GTA V",
                                 "type": "live"}}
    checker = HelixChecker("id", "secret", client=FakeHelix())
    assert await checker.check(["streamer", "other"]) == {
        "streamer": StreamInfo("9", "Day 2", "GTA V")}


def test_auto_monitor_endpoint_toggles_channel(factory):
    api = FastAPI()
    api.include_router(routes_channels.router, prefix="/api/v1")

    def _db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    api.dependency_overrides[get_db] = _db
    with TestClient(api) as c:
        created = c.post("/api/v1/channels", params={"twitch_login": "Streamer"}).json()
        assert created["auto_monitor"] is False
        cid = created["id"]
        assert c.put(f"/api/v1/channels/{cid}/auto-monitor", json={"enabled": True}).json() == {
            "id": cid, "auto_monitor": True}
        assert c.get("/api/v1/channels").json()[0]["auto_monitor"] is True
        assert c.put("/api/v1/channels/999/auto-monitor", json={"enabled": True}).status_code == 404

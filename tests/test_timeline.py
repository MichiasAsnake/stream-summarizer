from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_sessions
from app.db import models as m
from app.db.models import Base
from app.db.session import get_db
from app.summarize import beats

START = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _stream(db, status="live", ended=None):
    db.add(m.Channel(id=1, twitch_login="rp", created_at=START.isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status=status,
                     started_at=START.isoformat(), ended_at=ended))
    db.commit()


def _event(db, eid, minute, importance, text):
    """Event starting 30s into a 60s window that closed at `minute` + 1."""
    db.add(m.Window(id=eid, session_id=1, t_start=0, t_end=60, status="done",
                    created_at=(START + timedelta(minutes=minute + 1)).isoformat()))
    db.add(m.Event(id=eid, session_id=1, window_id=eid, t_start=30.0, description=text,
                   importance=importance))
    db.commit()


class FakeLLM:
    def __init__(self, beats_out=None, fail=False):
        self.beats_out, self.fail, self.calls = beats_out or [], fail, 0

    def generate_json(self, schema, prompt, system=""):
        self.calls += 1
        self.prompt, self.system = prompt, system
        if self.fail:
            raise TimeoutError("provider down")
        return {"beats": self.beats_out}


def test_event_times_use_wall_clock_since_stream_start(factory):
    db = factory()
    _stream(db)
    _event(db, 1, 42, 4, "Marcus robs the bank")
    (t, ev), = beats.timed_events(db, 1)
    assert t == 42 * 60 + 30 and ev.id == 1
    assert beats.clock(t) == "0:42"


def test_open_hour_shows_top_events_live(factory):
    db = factory()
    _stream(db)
    _event(db, 1, 5, 3, "minor logistics")
    for i, minute in enumerate([10, 20, 30, 40], start=2):
        _event(db, i, minute, 4 if i < 5 else 5, f"big moment {i}")
    items = beats.timeline(db, 1)
    assert [i["headline"] for i in items] == ["big moment 3", "big moment 4", "big moment 5"]
    assert {i["kind"] for i in items} == {"event"}


def test_closed_hour_becomes_grounded_beats_once(factory):
    db = factory()
    _stream(db)
    _event(db, 1, 10, 4, "Marcus plans the heist")
    _event(db, 2, 50, 5, "The heist goes wrong and Marcus is arrested")
    _event(db, 3, 70, 4, "Hour two event")
    llm = FakeLLM([
        {"headline": "Marcus plans the heist", "significance": 4, "event_ids": [1]},
        {"headline": "Heist fails; Marcus arrested", "significance": 5, "event_ids": [2, 99]},
        {"headline": "Invented moment", "significance": 5, "event_ids": [99]},
    ])
    now = START + timedelta(hours=1, minutes=20)
    assert beats.due_hours(db, now) == [(1, 0)]
    assert beats.generate_due_beats(factory, lambda: llm, now=now) == 2
    assert "id 3" not in llm.prompt and "wrap" not in llm.system
    assert beats.due_hours(db, now) == []  # exactly once
    items = beats.timeline(db, 1)
    assert [(i["clock"], i["headline"], i["kind"]) for i in items] == [
        ("0:10", "Marcus plans the heist", "beat"),
        ("0:50", "Heist fails; Marcus arrested", "beat"),
        ("1:10", "Hour two event", "event"),  # hour 2 still open
    ]


def test_quiet_hour_is_skipped_without_llm_and_failures_retry(factory):
    db = factory()
    _stream(db, status="ended", ended=(START + timedelta(hours=1, minutes=30)).isoformat())
    _event(db, 1, 10, 2, "filler")
    _event(db, 2, 70, 4, "real moment")
    now = START + timedelta(hours=3)
    failing = FakeLLM(fail=True)
    assert beats.generate_due_beats(factory, lambda: failing, now=now) == 0
    assert failing.calls == 1  # hour 1 had nothing notable: no call
    assert beats.due_hours(db, now) == [(1, 1)]  # failed hour stays due
    ok = FakeLLM([{"headline": "Real moment", "significance": 4, "event_ids": [2]}])
    assert beats.generate_due_beats(factory, lambda: ok, now=now) == 1
    assert beats.due_hours(db, now) == []


def test_closed_hours_respects_grace_and_session_end():
    live = m.Session(status="live", started_at=START.isoformat())
    assert beats.closed_hours(live, START + timedelta(hours=2, minutes=2)) == 1
    assert beats.closed_hours(live, START + timedelta(hours=2, minutes=4)) == 2
    ended = m.Session(status="ended", started_at=START.isoformat(),
                      ended_at=(START + timedelta(hours=2, minutes=5)).isoformat())
    assert beats.closed_hours(ended, START) == 3


def test_timeline_endpoint(factory):
    db = factory()
    _stream(db)
    _event(db, 1, 15, 5, "Big reveal")
    api = FastAPI()
    api.include_router(routes_sessions.router, prefix="/api/v1")
    api.dependency_overrides[get_db] = lambda: factory()
    with TestClient(api) as c:
        body = c.get("/api/v1/sessions/1/timeline").json()
        assert body == [{"at": 930.0, "clock": "0:15", "hour": 0, "headline": "Big reveal",
                         "significance": 5, "kind": "event"}]
        assert c.get("/api/v1/sessions/9/timeline").status_code == 404

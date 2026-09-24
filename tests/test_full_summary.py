from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes_sessions
from app.db import models as m
from app.db.models import Base


def test_full_summary_regenerates_only_on_material_change(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="live"))
    db.add(m.Event(session_id=1, description="Opening scene", importance=3))
    db.commit()
    calls = []

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            if "Condense" in kwargs["system"]:
                calls.append(("preview", prompt))
                return f"Short: {prompt}"
            calls.append(("full", prompt))
            return f"Full story version {sum(kind == 'full' for kind, _ in calls)}"

    monkeypatch.setattr(routes_sessions, "get_llm", lambda _kind: StubLlm())
    first = routes_sessions.get_full_summary(1, db)
    assert first["text"] == "Full story version 1"
    assert first["preview"] == "Short: Full story version 1"
    assert routes_sessions.get_full_summary(1, db)["cached"] is True
    assert len(calls) == 2

    db.add(m.Event(session_id=1, description="A deal was made", importance=4))
    db.commit()
    second = routes_sessions.get_full_summary(1, db)
    assert second["text"] == "Full story version 2"
    assert second["preview"] == "Short: Full story version 2"
    assert "Full story version 1" in calls[2][1]
    assert routes_sessions.get_full_summary(1, db)["cached"] is True
    assert len(calls) == 4


def test_full_summary_is_returned_when_preview_generation_fails(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="live"))
    db.add(m.Event(session_id=1, description="Opening scene", importance=3))
    db.commit()

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            if "Condense" in kwargs["system"]:
                raise RuntimeError("preview failed")
            return "A complete account of the stream."

    monkeypatch.setattr(routes_sessions, "get_llm", lambda _kind: StubLlm())
    result = routes_sessions.get_full_summary(1, db)
    assert result["text"] == "A complete account of the stream."
    assert result["preview"] is None


def test_full_summary_ignores_old_threads_and_contaminated_cache(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="ended"))
    db.add(m.Session(id=2, channel_id=1, source="live", status="live"))
    db.add(m.Thread(channel_id=1, title="Cargo heist", summary="Eight loads arrive tomorrow",
                    status="open", last_updated_at="2026-01-01"))
    db.add(m.Event(session_id=1, description="Cargo heist postponed", importance=4))
    db.add(m.Event(session_id=2, description="Foundry door clue discovered", importance=4))
    db.add(m.Summary(session_id=2, kind="full", text="Cargo heist postponed",
                     cache_key="2:old-thread-stamp", created_at=datetime.now(UTC).isoformat()))
    db.commit()
    prompts = []

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            prompts.append(prompt)
            return "Foundry door clue discovered"

    monkeypatch.setattr(routes_sessions, "get_llm", lambda _kind: StubLlm())
    first = routes_sessions.get_full_summary(2, db)
    assert first["text"] == "Foundry door clue discovered"
    assert "Foundry door clue discovered" in prompts[0]
    assert "Cargo heist" not in prompts[0]
    assert "Eight loads" not in prompts[0]
    assert routes_sessions.get_full_summary(2, db)["cached"] is True
    db.add(m.Event(session_id=2, description="New foundry code found", importance=3))
    db.commit()
    routes_sessions.get_full_summary(2, db)
    assert "Foundry door clue discovered" in prompts[2]
    assert "Cargo heist" not in prompts[2]


def test_full_summary_waits_for_current_session_events(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="live"))
    db.add(m.Thread(channel_id=1, title="Old plan", status="open"))
    db.commit()
    monkeypatch.setattr(routes_sessions, "get_llm", lambda _kind: (_ for _ in ()).throw(
        AssertionError("should not generate without events")))
    result = routes_sessions.get_full_summary(1, db)
    assert result["text"] == ""
    assert result["id"] is None


def test_minor_events_are_batched_but_notable_events_refresh_immediately(monkeypatch):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="live"))
    db.add(m.Event(session_id=1, description="First door clue", importance=2))
    db.commit()
    calls = []

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            calls.append(prompt)
            return "Overview of the stream"

    monkeypatch.setattr(routes_sessions, "get_llm", lambda _kind: StubLlm())
    routes_sessions.get_full_summary(1, db)
    first_key = routes_sessions.recap_status(1, db)["full_key"]
    for description in ("Second code tried", "Third code tried"):
        db.add(m.Event(session_id=1, description=description, importance=2))
        db.commit()
        assert routes_sessions.recap_status(1, db)["full_key"] == first_key
        assert routes_sessions.get_full_summary(1, db)["cached"] is True
    assert len(calls) == 2  # full and preview, only once

    db.add(m.Event(session_id=1, description="New location discovered", importance=2))
    db.commit()
    assert routes_sessions.recap_status(1, db)["full_key"] != first_key
    assert routes_sessions.get_full_summary(1, db)["cached"] is False

    db.add(m.Event(session_id=1, description="Major reveal", importance=4))
    db.commit()
    assert routes_sessions.get_full_summary(1, db)["cached"] is False

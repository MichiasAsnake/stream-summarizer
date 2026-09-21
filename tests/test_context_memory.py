import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.llm.schemas import Extraction
from app.memory.context import append_bounded, build_context, snippet
from app.memory.extractor import run_extraction
from app.memory.resolution import touch_entity
from app.memory.writer import write_extraction

T0 = "2026-09-0{}T20:00:00+00:00"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.add(m.Channel(id=1, twitch_login="streamer", created_at=T0.format(1),
                    config_json=json.dumps({"streamer": {"name": "Sam", "entity_id": 1}})))
    s.commit()
    return s


def _session(db, sid, day, final=None, title=None):
    db.add(m.Session(id=sid, channel_id=1, source="live", status="ended" if final else "live",
                     started_at=T0.format(day), final_summary=final, title=title))
    db.commit()


def _entity(db, eid, name, mentions=1, seen=T0.format(1), status="confirmed", aliases=()):
    db.add(m.Entity(id=eid, channel_id=1, type="person", canonical_name=name, status=status,
                    mention_count=mentions, last_seen_at=seen, description=f"{name} desc"))
    for a in aliases:
        db.add(m.EntityAlias(entity_id=eid, alias=a))
    db.commit()


def test_bounded_append_keeps_origin_and_newest_information():
    text = "Origin: a knight. " + "x" * 3000
    out = append_bounded(text, "LATEST: now a king")
    assert len(out) <= 2000
    assert out.startswith("Origin: a knight.") and out.endswith("LATEST: now a king")
    assert snippet("A" * 50 + "END", 30).endswith("END")


def test_touch_entity_no_longer_drops_new_facts():
    ent = m.Entity(channel_id=1, type="person", canonical_name="Kael", status="confirmed",
                   mention_count=5, description="y" * 1995)
    touch_entity(None, ent, "betrayed the guild")
    assert ent.description.endswith("betrayed the guild")


def test_cast_prefers_streamer_and_people_named_in_the_window(db):
    _session(db, 1, 5)
    _entity(db, 1, "Sam", mentions=1)
    for i in range(2, 16):  # popular regulars fill the all-time top 12
        _entity(db, i, f"Regular{i}", mentions=100)
    _entity(db, 20, "Kael Stormborn", mentions=1, aliases=["Kael"])
    _entity(db, 21, "Old Duplicate", mentions=500, status="merged")
    ctx = build_context(db, 1, 1, [], window_text="then kael pulled a knife")
    cast = [line for line in ctx.splitlines() if line.startswith("[cast]")]
    assert cast[0].startswith("[cast] Sam") and cast[0].endswith("[STREAMER]")
    assert cast[1].startswith("[cast] Kael Stormborn aka Kael")
    assert len(cast) == 12
    assert "Old Duplicate" not in ctx


def test_dormant_threads_drop_out_unless_mentioned(db):
    for sid in range(1, 5):
        _session(db, sid, sid)
    db.add_all([
        m.Thread(id=1, channel_id=1, title="Warehouse heist", status="open",
                 last_updated_at="2026-08-31T21:00:00+00:00"),  # before the last 3 sessions
        m.Thread(id=2, channel_id=1, title="Bank job", status="open",
                 last_updated_at="2026-09-03T21:00:00+00:00"),
        m.Thread(id=3, channel_id=1, title="Old feud", status="open",
                 last_updated_at="2026-08-01T21:00:00+00:00"),
    ])
    db.commit()
    quiet = build_context(db, 1, 4, [], window_text="nothing relevant")
    assert "Bank job" in quiet and "Warehouse heist" not in quiet and "Old feud" not in quiet
    revived = build_context(db, 1, 4, [], window_text="remember the old feud?")
    assert revived.index("Old feud") < revived.index("Bank job")


def test_previously_on_and_in_session_history_are_included(db):
    _session(db, 1, 1, final="Sam won the tournament.", title="Finals day")
    _session(db, 2, 2)
    db.add(m.Window(id=1, session_id=2, t_start=0, t_end=60, status="done",
                    window_summary="Sam arrived at the docks.", created_at=T0.format(2)))
    db.add(m.Summary(session_id=2, kind="rolling", text="sam is scoping the docks 👀",
                     created_at=T0.format(2)))
    db.commit()
    ctx = build_context(db, 1, 2, [])
    assert "[previously 2026-09-01 Finals day] Sam won the tournament." in ctx
    assert "[history] Sam arrived at the docks." in ctx
    assert "[rolling] sam is scoping the docks" in ctx
    assert "[previously" not in build_context(db, 1, 1, [])  # never its own summary


def test_thread_importance_tracks_its_biggest_event(db):
    _session(db, 1, 1)
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=1, status="done",
                    created_at=T0.format(1)))
    db.commit()
    write_extraction(db, 1, 1, 1, Extraction.model_validate({
        "thread_updates": [{"ref": "NEW:Heist", "status": "open", "delta": "planned",
                            "confidence": 0.9}],
        "events": [{"t_start": 0, "t_end": 1, "type": "plot", "description": "vault opened",
                    "importance": 5, "thread_ref": "NEW:Heist", "confidence": 0.9}]}))
    assert db.query(m.Thread).one().importance == 5


def test_extraction_prompt_receives_window_relevant_cast(db):
    _session(db, 1, 1)
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=1, status="pending",
                    created_at=T0.format(1)))
    db.commit()
    for i in range(2, 16):
        _entity(db, i, f"Regular{i}", mentions=100)
    _entity(db, 20, "Kael", mentions=1)

    class CaptureLLM:
        prompt = ""

        def generate_json(self, schema, prompt, system=""):
            CaptureLLM.prompt = prompt
            return {"window_summary": "Kael spoke."}

    run_extraction(db, 1, 1, 1, [{"id": 1, "t_start": 0.0, "t_end": 1.0,
                                  "text": "Kael is here", "speaker": "S1", "conf": 0.9}],
                   CaptureLLM())
    assert "[cast] Kael" in CaptureLLM.prompt

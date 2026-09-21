import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.llm.schemas import EventOut, Extraction, normalize_streamer_role
from app.memory.writer import write_extraction


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    from app.db import models as m
    db.add(m.Channel(id=2, twitch_login="xqc", created_at="2026-09-21T00:00:00+00:00"))
    db.add(m.Session(id=1, channel_id=2, source="live", status="live"))
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=60, status="done",
                    created_at="2026-09-21T00:00:00+00:00"))
    db.commit()
    return db


def test_normalize_valid_roles_kept():
    for r in ("actor", "target", "witness", "informed", "ambient", "offscreen"):
        assert normalize_streamer_role(r) == r


def test_normalize_invalid_to_ambient():
    assert normalize_streamer_role("protagonist") == "ambient"
    assert normalize_streamer_role("") == "ambient"
    assert normalize_streamer_role(None) == "ambient"
    assert normalize_streamer_role(" Actor ") == "actor"


def test_event_out_never_fails_on_role():
    e = EventOut.model_validate({"t_start": 0, "t_end": 1, "type": "banter",
                                 "description": "x", "streamer_role": "hero"})
    assert e.streamer_role == "ambient"
    e2 = EventOut.model_validate({"t_start": 0, "t_end": 1, "type": "banter",
                                  "description": "x"})
    assert e2.streamer_role == "ambient"


def test_writer_persists_role():
    from app.db import models as m
    db = _db()
    ext = Extraction.model_validate({
        "events": [
            {"t_start": 0, "t_end": 1, "type": "dialogue", "description": "he acts",
             "streamer_role": "actor"},
            {"t_start": 1, "t_end": 2, "type": "banter", "description": "bg",
             "streamer_role": "bogus"},
        ]})
    write_extraction(db, 2, 1, 1, ext)
    roles = sorted(r[0] for r in db.execute(
        __import__("sqlalchemy").select(m.Event.streamer_role)).fetchall())
    assert roles == ["actor", "ambient"]


def test_migration_adds_column(tmp_path):
    from app.db.session import ensure_schema
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id INTEGER)")
    con.execute("CREATE TABLE windows (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE ingest_gaps (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    ensure_schema(f"sqlite:///{p}")
    con = sqlite3.connect(p)
    cols = [r[1] for r in con.execute("PRAGMA table_info(events)").fetchall()]
    con.close()
    assert "streamer_role" in cols

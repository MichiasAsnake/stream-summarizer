from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.memory import streamer as st

STREAMER_CFG = {
    "name": "X",
    "entity_id": 5,
    "aliases": ["xqc", "jp", "Jean-Paul", "Mr. Paul"],
    "characters": [{"name": "paul", "context": "GTA RP police character"}],
    "pov_mode": "participant",
}


def _db(config=None):
    import json
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=2, twitch_login="xqc",
                     config_json=json.dumps(config) if config else None,
                     created_at="2026-09-21T00:00:00+00:00"))
    db.commit()
    return db


def test_config_parses():
    db = _db({"streamer": STREAMER_CFG})
    got = st.get_streamer(db, 2)
    assert got["name"] == "X"
    assert got["entity_id"] == 5
    assert got["pov_mode"] == "participant"
    assert got["source"] == "config"
    assert got["characters"][0]["name"] == "paul"


def test_fallback_to_login():
    db = _db()
    got = st.get_streamer(db, 2)
    assert got["name"] == "xqc"
    assert got["source"] == "fallback"
    assert got["entity_id"] is None


def test_bad_mode_defaults_participant():
    cfg = dict(STREAMER_CFG, pov_mode="omniscient")
    db = _db({"streamer": cfg})
    assert st.get_streamer(db, 2)["pov_mode"] == "participant"


def test_observer_mode_kept():
    cfg = dict(STREAMER_CFG, pov_mode="observer")
    db = _db({"streamer": cfg})
    assert st.get_streamer(db, 2)["pov_mode"] == "observer"


def test_is_streamer_entity_guard():
    db = _db({"streamer": STREAMER_CFG})
    assert st.is_streamer_entity(db, 2, 5) is True
    assert st.is_streamer_entity(db, 2, 6) is False
    assert st.is_streamer_entity(db, 2, None) is False


def test_unknown_channel_fallback():
    db = _db()
    got = st.get_streamer(db, 999)
    assert got["source"] == "fallback"

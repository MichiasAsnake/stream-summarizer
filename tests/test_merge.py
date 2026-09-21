import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.memory.merge import candidate_pairs, suggest_merges


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _seed(db):
    from app.db import models as m
    a = m.Entity(channel_id=2, type="person", canonical_name="Marcus",
                 status="confirmed", mention_count=5)
    b = m.Entity(channel_id=2, type="person", canonical_name="Marcus Webb",
                 status="provisional", mention_count=1)
    c = m.Entity(channel_id=2, type="person", canonical_name="Priya Nair",
                 status="confirmed", mention_count=4)
    db.add_all([a, b, c])
    db.flush()
    db.add_all([
        m.EntityAlias(entity_id=a.id, alias="Marcus"),
        m.EntityAlias(entity_id=b.id, alias="Marcus Webb"),
        m.EntityAlias(entity_id=c.id, alias="Priya Nair"),
    ])
    db.commit()
    return a, b, c


def test_candidate_pairs_fuzzy_match():
    db = _db()
    a, b, c = _seed(db)
    pairs = candidate_pairs(db, 2)
    ids = {(x.id, y.id) for x, y in pairs}
    assert (a.id, b.id) in ids
    assert all(c.id not in (x.id, y.id) for x, y in pairs)


def test_candidate_pairs_shared_alias():
    from app.db import models as m
    db = _db()
    a, b, c = _seed(db)
    other = m.Entity(channel_id=2, type="person", canonical_name="Bobby",
                     status="provisional", mention_count=1)
    db.add(other)
    db.flush()
    db.add(m.EntityAlias(entity_id=other.id, alias="marcus"))
    db.commit()
    pairs = candidate_pairs(db, 2)
    assert any({x.id, y.id} == {a.id, other.id} for x, y in pairs)


def test_suggest_merges_thresholds_and_direction():
    db = _db()
    a, b, c = _seed(db)

    class Stub:
        def ask(self, state, questions):
            assert "Zuck" not in state  # state lists seeded entities only
            assert set(questions) == {f"pair_{a.id}_{b.id}"}
            return {"model": "jev-1.13.0",
                    "answers": {f"pair_{a.id}_{b.id}": {"noul": 0.82}}}

    out = suggest_merges(db, 2, classifier=Stub())
    assert len(out) == 1
    s = out[0]
    assert s["from_id"] == b.id and s["into_id"] == a.id  # lower id absorbs
    assert s["band"] == "merge" and s["model"] == "jev-1.13.0"

    class ReviewStub:
        def ask(self, state, questions):
            k = next(iter(questions))
            return {"model": "m", "answers": {k: {"noul": 0.6}}}

    out = suggest_merges(db, 2, classifier=ReviewStub())
    assert len(out) == 1 and out[0]["band"] == "review"

    class LowStub:
        def ask(self, state, questions):
            k = next(iter(questions))
            return {"model": "m", "answers": {k: {"noul": 0.2}}}

    assert suggest_merges(db, 2, classifier=LowStub()) == []


def test_suggest_merges_no_pairs():
    db = _db()
    assert suggest_merges(db, 2, classifier=object()) == []


def test_suggest_merges_requires_jev(monkeypatch):
    import app.memory.merge as M

    db = _db()
    _seed(db)
    monkeypatch.setattr(M, "get_classifier", lambda *a, **k: object())
    with pytest.raises(RuntimeError):
        suggest_merges(db, 2)


def test_streamer_pair_capped_at_review():
    import json

    from app.db import models as m
    from app.memory.merge import suggest_merges

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=2, twitch_login="xqc",
                     config_json=json.dumps({"streamer": {"name": "X", "entity_id": 1}}),
                     created_at="2026-09-21T00:00:00+00:00"))
    a = m.Entity(channel_id=2, type="person", canonical_name="X",
                 status="confirmed", mention_count=8)
    b = m.Entity(channel_id=2, type="person", canonical_name="Paul",
                 status="provisional", mention_count=1)
    db.add_all([a, b])
    db.flush()
    db.add_all([m.EntityAlias(entity_id=a.id, alias="X"),
                m.EntityAlias(entity_id=b.id, alias="X")])
    db.commit()

    class HotStub:
        def ask(self, state, questions):
            k = next(iter(questions))
            return {"model": "m", "answers": {k: {"noul": 0.95}}}

    out = suggest_merges(db, 2, classifier=HotStub())
    assert len(out) == 1
    assert out[0]["band"] == "review"  # never auto-merge the streamer entity

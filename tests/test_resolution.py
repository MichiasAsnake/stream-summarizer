from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base
from app.memory.resolution import resolve_entity


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_exact_alias_resolves():
    db = _db()
    e1, created = resolve_entity(db, 1, "NEW:Kael", "character")
    assert created
    e2, created2 = resolve_entity(db, 1, "kael", "character")
    assert not created2 and e2.id == e1.id


def test_provisional_promotion():
    from app.memory.resolution import touch_entity
    db = _db()
    e, _ = resolve_entity(db, 1, "NEW:Zara", "character")
    assert e.status == "provisional"
    touch_entity(db, e)
    touch_entity(db, e)
    assert e.status == "confirmed"


def test_short_ref_exact_still_resolves():
    db = _db()
    e1, _ = resolve_entity(db, 1, "NEW:X", "person")
    e2, created = resolve_entity(db, 1, "X", "person")
    assert not created and e2.id == e1.id


def test_short_ref_skips_fuzzy():
    # "Ma" would fuzzy-match "Max" via token-set; the min-length guard
    # must prevent that and create a provisional instead.
    db = _db()
    e1, _ = resolve_entity(db, 1, "NEW:Max", "person")
    e2, created = resolve_entity(db, 1, "Ma", "person")
    assert created and e2.id != e1.id

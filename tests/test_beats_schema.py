import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.session import ensure_schema


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def test_beat_tables_exist_and_link():
    from datetime import datetime, timezone
    from app.db import models as m
    eng, db = _db()
    now = datetime.now(timezone.utc).isoformat()
    beat = m.PlotBeat(session_id=1, hour_index=3, run_label="live",
                      wall_start=now, wall_end=now, t_start=10.0, t_end=99.0,
                      headline="Arrest made", context="X was arrested.",
                      significance=5, status="final", prompt_version="beats-v1",
                      model="m", tokens_in=100, tokens_out=50, cost_usd=0.001,
                      created_at=now)
    db.add(beat)
    db.commit()
    db.add(m.BeatEvent(beat_id=beat.id, event_id=7))
    db.add(m.BeatRun(session_id=1, hour_index=3, run_label="live",
                     status="done", reason=None, created_at=now))
    db.commit()
    assert db.get(m.PlotBeat, beat.id).headline == "Arrest made"
    assert db.query(m.BeatEvent).count() == 1
    with pytest.raises(IntegrityError):
        db.add(m.BeatRun(session_id=1, hour_index=3, run_label="live",
                         status="done", reason=None, created_at=now))
        db.commit()
    db.rollback()
    # a second run label may cover the same hour
    db.add(m.BeatRun(session_id=1, hour_index=3, run_label="exp-v2",
                     status="done", reason=None, created_at=now))
    db.commit()


def test_boot_epoch_columns():
    from app.db import models as m
    eng, db = _db()
    w = m.Window(session_id=1, t_start=0.0, t_end=60.0, status="done",
                 boot_epoch=123, created_at="2026-01-01T00:00:00+00:00")
    db.add(w)
    db.commit()
    assert db.get(m.Window, w.id).boot_epoch == 123


def test_ensure_schema_adds_missing_columns():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE windows (id INTEGER PRIMARY KEY, session_id INTEGER, "
                          "t_start REAL, t_end REAL, status TEXT, created_at TEXT)"))
        conn.execute(text("CREATE TABLE ingest_gaps (id INTEGER PRIMARY KEY, session_id INTEGER, "
                          "t_start REAL, t_end REAL, reason TEXT)"))
    ensure_schema(eng)
    with eng.begin() as conn:
        wcols = [r[1] for r in conn.execute(text("PRAGMA table_info(windows)")).fetchall()]
        gcols = [r[1] for r in conn.execute(text("PRAGMA table_info(ingest_gaps)")).fetchall()]
        tables = [r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
    assert "boot_epoch" in wcols and "boot_epoch" in gcols
    assert {"plot_beats", "beat_runs", "beat_events"} <= set(tables)
    ensure_schema(eng)  # idempotent second run

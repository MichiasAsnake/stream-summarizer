from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.ingest.supervisor import GapEvent, IngestSupervisor
from app.pipeline import reconcile_unobserved_gap


def test_emit_gap_appends_and_fires():
    sup = IngestSupervisor(("cmd",), on_gap=None)
    seen = []
    sup.on_gap = seen.append
    g = sup._emit_gap(1.0, 2.0, "reconnect")
    assert isinstance(g, GapEvent)
    assert sup.gaps == [g] and seen == [g]
    assert g.written is False


def test_emit_gap_callback_exception_swallowed():
    def bad(g):
        raise RuntimeError("db down")
    sup = IngestSupervisor(("cmd",), on_gap=bad)
    g = sup._emit_gap(0, 0, "exit")
    assert sup.gaps == [g]  # still recorded; drain retries


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


NOW = "2026-09-21T08:00:00+00:00"


def _seed_window(db, created_at):
    db.add(m.Channel(id=2, twitch_login="xqc", created_at=NOW))
    db.add(m.Session(id=1, channel_id=2, source="live", status="live"))
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=60, status="done",
                    created_at=created_at))
    db.commit()


def test_reconcile_old_hole_recorded():
    db = _db()
    _seed_window(db, "2026-09-21T07:30:00+00:00")
    row = reconcile_unobserved_gap(db, 1, 999, now_iso=NOW)
    assert row is not None
    assert row.boot_epoch == 999 and row.t_start == 0 and row.t_end == 0
    assert "unobserved worker-down" in row.reason and "07:30" in row.reason


def test_reconcile_recent_no_row():
    db = _db()
    _seed_window(db, "2026-09-21T07:59:00+00:00")
    assert reconcile_unobserved_gap(db, 1, 999, now_iso=NOW) is None
    assert db.query(m.IngestGap).count() == 0


def test_reconcile_no_windows_no_row():
    db = _db()
    assert reconcile_unobserved_gap(db, 1, 999, now_iso=NOW) is None

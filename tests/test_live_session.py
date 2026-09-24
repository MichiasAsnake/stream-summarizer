from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_channels
from app.db import models as m
from app.db.models import Base
from app.db.session import get_db

START = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)


def make_client():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    db = factory()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=START.isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="ended",
                     started_at=START.isoformat(),
                     ended_at=(START).isoformat()))
    db.add(m.Session(id=2, channel_id=1, source="live", status="live",
                     started_at=START.isoformat()))
    db.commit()
    db.close()
    api = FastAPI()
    api.include_router(routes_channels.router, prefix="/api/v1")
    api.dependency_overrides[get_db] = lambda: factory()
    return TestClient(api)


def test_resolves_live_session_case_insensitive():
    c = make_client()
    body = c.get("/api/v1/channels/XQC/live-session").json()
    assert body == {"channel_id": 1, "session_id": 2}


def test_unknown_channel_404():
    c = make_client()
    assert c.get("/api/v1/channels/someone/live-session").status_code == 404


def test_no_live_session_404():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    db = factory()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=START.isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="ended",
                     started_at=START.isoformat(),
                     ended_at=START.isoformat()))
    db.commit()
    db.close()
    api = FastAPI()
    api.include_router(routes_channels.router, prefix="/api/v1")
    api.dependency_overrides[get_db] = lambda: factory()
    c = TestClient(api)
    assert c.get("/api/v1/channels/xqc/live-session").status_code == 404


def test_summary_session_falls_back_to_latest_session_with_summary():
    c = make_client()
    assert c.get("/api/v1/channels/xqc/summary-session").json() == {
        "channel_id": 1, "session_id": 2, "live": True}
    from app.db.session import get_db

    db = c.app.dependency_overrides[get_db]()
    db.query(m.Session).filter(m.Session.id == 2).update({"status": "ended"})
    db.add(m.Summary(session_id=1, kind="rolling", text="Earlier stream",
                     created_at=START.isoformat()))
    db.commit()
    assert c.get("/api/v1/channels/xqc/summary-session").json() == {
        "channel_id": 1, "session_id": 1, "live": False}
    db.add(m.Summary(session_id=2, kind="full", text="Latest stream",
                     created_at=START.isoformat()))
    db.commit()
    assert c.get("/api/v1/channels/xqc/summary-session").json() == {
        "channel_id": 1, "session_id": 2, "live": False}
    db.close()


def test_summary_session_404_without_any_saved_summary():
    c = make_client()
    from app.db.session import get_db

    db = c.app.dependency_overrides[get_db]()
    db.query(m.Session).filter(m.Session.id == 2).update({"status": "ended"})
    db.commit()
    assert c.get("/api/v1/channels/xqc/summary-session").status_code == 404
    db.close()

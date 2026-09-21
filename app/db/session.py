"""DB engine/session helpers. SQLite WAL mode per §4."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import Base


def get_engine(url: str | None = None):
    url = url or settings.DB_URL
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _wal(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()
    return engine


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(url: str | None = None) -> None:
    eng = get_engine(url) if url else engine
    Base.metadata.create_all(eng)


def ensure_schema(url_or_engine=None) -> None:
    """Idempotent Step-2 migration: new tables via create_all, new columns
    via ALTER TABLE when missing (PRAGMA check, no exception-driven flow)."""
    from sqlalchemy import text as _text
    eng = url_or_engine if url_or_engine is not None and not isinstance(url_or_engine, str) \
        else get_engine(url_or_engine)
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        for table, column, ddl in (
            ("windows", "boot_epoch", "ALTER TABLE windows ADD COLUMN boot_epoch INTEGER"),
            ("ingest_gaps", "boot_epoch", "ALTER TABLE ingest_gaps ADD COLUMN boot_epoch INTEGER"),
            ("events", "streamer_role", "ALTER TABLE events ADD COLUMN streamer_role TEXT"),
            ("summaries", "cache_key", "ALTER TABLE summaries ADD COLUMN cache_key TEXT"),
            ("summaries", "tokens_in", "ALTER TABLE summaries ADD COLUMN tokens_in INTEGER"),
            ("summaries", "tokens_out", "ALTER TABLE summaries ADD COLUMN tokens_out INTEGER"),
            ("summaries", "cost_usd", "ALTER TABLE summaries ADD COLUMN cost_usd REAL"),
            ("sessions", "last_error", "ALTER TABLE sessions ADD COLUMN last_error TEXT"),
            ("sessions", "last_error_at", "ALTER TABLE sessions ADD COLUMN last_error_at TEXT"),
        ):
            cols = [r[1] for r in conn.execute(_text(f"PRAGMA table_info({table})")).fetchall()]
            if column not in cols:
                conn.execute(_text(ddl))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

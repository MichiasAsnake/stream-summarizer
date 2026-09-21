"""Run a finite replay through the production pipeline and optionally score it."""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.db.session import ensure_schema
from app.ingest.supervisor import build_replay_cmd
from app.llm.base import get_llm
from app.pipeline import PipelineConfig, run_session
from app.summarize.rolling import build_recap
from eval.metrics import evaluate_snapshot


def run_replay_stub(input_path: str) -> dict:
    """Fast compatibility check used when external media tools are unavailable."""
    path = Path(input_path)
    return {"input": str(path), "bytes": path.stat().st_size if path.exists() else 0,
            "status": "stub-ok"}


def _snapshot(db, session_id: int, input_path: str, recap: str) -> dict:
    session = db.get(m.Session, session_id)
    segments = db.execute(select(m.Segment).where(
        m.Segment.session_id == session_id).order_by(m.Segment.t_start)).scalars().all()
    windows = db.execute(select(m.Window).where(
        m.Window.session_id == session_id).order_by(m.Window.id)).scalars().all()
    events = db.execute(select(m.Event).where(
        m.Event.session_id == session_id).order_by(m.Event.id)).scalars().all()
    summaries = db.execute(select(m.Summary).where(
        m.Summary.session_id == session_id).order_by(m.Summary.id)).scalars().all()
    channel_id = session.channel_id if session else -1
    entities = db.execute(select(m.Entity).where(
        m.Entity.channel_id == channel_id, m.Entity.status != "merged")).scalars().all()
    threads = db.execute(select(m.Thread).where(
        m.Thread.channel_id == channel_id)).scalars().all()
    return {
        "input": input_path,
        "session_id": session_id,
        "status": session.status if session else "missing",
        "last_error": session.last_error if session else "session missing",
        "segments": len(segments),
        "windows": len(windows),
        "completed_windows": sum(w.status == "done" for w in windows),
        "failed_windows": sum(w.status == "failed" for w in windows),
        "transcript": " ".join(s.text for s in segments),
        "window_summaries": [w.window_summary for w in windows if w.window_summary],
        "events": [e.description for e in events],
        "entities": [e.canonical_name for e in entities],
        "threads": [t.title for t in threads],
        "rolling_summaries": [s.text for s in summaries if s.kind == "rolling"],
        "recap": recap,
    }


async def replay_file(input_path: str, channel_id: int = 1,
                      db_url: str | None = None, gold: dict | None = None) -> dict:
    """Exercise ingest, VAD, ASR, windowing, extraction, memory, and summaries.

    Runs in an isolated temporary SQLite database unless ``db_url`` is
    explicitly supplied, so evaluations cannot pollute application memory.
    """
    resolved = str(Path(input_path).expanduser().resolve(strict=True))
    temp_dir = tempfile.TemporaryDirectory(prefix="stream-summary-eval-")
    try:
        eval_url = db_url or f"sqlite:///{Path(temp_dir.name) / 'eval.db'}"
        engine = create_engine(
            eval_url,
            connect_args={"check_same_thread": False} if eval_url.startswith("sqlite") else {})
        Base.metadata.create_all(engine)
        ensure_schema(engine)
        db_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        db = db_factory()
        try:
            channel = db.get(m.Channel, channel_id)
            if channel is None:
                channel = m.Channel(
                    id=channel_id, twitch_login=f"eval_{channel_id}",
                    created_at=datetime.now(UTC).isoformat())
                db.add(channel)
            session = m.Session(
                channel_id=channel_id, source="replay", status="live",
                title=f"eval:{Path(resolved).name}", started_at=datetime.now(UTC).isoformat())
            db.add(session)
            db.commit()
            db.refresh(session)
            session_id = session.id
        finally:
            db.close()

        await run_session(
            PipelineConfig(session_id=session_id, channel_id=channel_id,
                           cmd=build_replay_cmd(resolved), reconnect=False),
            db_factory=db_factory)

        db = db_factory()
        try:
            recap = build_recap(db, session_id, get_llm("recap"))
            result = _snapshot(db, session_id, resolved, recap)
        finally:
            db.close()
        if gold is not None:
            result["metrics"] = evaluate_snapshot(result, gold)
        return result
    finally:
        temp_dir.cleanup()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--gold", help="JSON gold file; see eval/gold/README.md")
    parser.add_argument("--db-url", help="Optional persistent evaluation database URL")
    parser.add_argument("--stub", action="store_true")
    args = parser.parse_args()
    if args.stub:
        result = run_replay_stub(args.input)
    else:
        gold = json.loads(Path(args.gold).read_text()) if args.gold else None
        result = await replay_file(args.input, args.channel, args.db_url, gold)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

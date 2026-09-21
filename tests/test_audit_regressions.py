import asyncio
import io
import sys
import time
import wave
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes_sessions
from app.api.routes_speakers import _decode_enrollment_wav
from app.audio.segmenter import Segmenter
from app.config import settings
from app.db import models as m
from app.db.models import Base
from app.ingest.supervisor import IngestSupervisor, build_live_cmd, build_replay_cmd
from app.llm.base import get_llm
from app.llm.schemas import Extraction
from app.memory.writer import write_extraction
from app.retention import enforce_retention


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_ingest_commands_keep_untrusted_values_as_single_arguments():
    payload = "name; touch /tmp/should-not-exist"
    live = build_live_cmd(payload)
    replay = build_replay_cmd(payload)
    assert live[-1] == payload
    assert replay[replay.index("-i") + 1] == payload
    with pytest.raises(TypeError):
        IngestSupervisor(payload)


@pytest.mark.asyncio
async def test_live_ingest_timeout_stops_silent_child_process():
    supervisor = IngestSupervisor(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        reconnect=True, offline_timeout_seconds=0.05)
    started = time.monotonic()
    chunks = [chunk async for chunk in supervisor.frames()]
    assert chunks == []
    assert time.monotonic() - started < 1
    assert supervisor._proc is None


def test_stateful_segmenter_preserves_origin_and_drops_silence_only_segments():
    segmenter = Segmenter(use_silero=False)
    frame = 512
    frames = ([np.full(frame, 0.1, dtype="float32") for _ in range(20)]
              + [np.zeros(frame, dtype="float32") for _ in range(50)])
    out = []
    for index, samples in enumerate(frames):
        out.extend(segmenter.add_chunk(samples, index * frame / 16000))
    assert len(out) == 1
    assert out[0].t_start == pytest.approx(0.0)
    assert out[0].t_end == pytest.approx(len(out[0].pcm) / 16000)


def test_voiceprint_wav_decoder_accepts_safe_mono_audio():
    raw = io.BytesIO()
    with wave.open(raw, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes((np.full(16000, 1000, dtype=np.int16)).tobytes())
    pcm = _decode_enrollment_wav(raw.getvalue())
    assert pcm.shape == (16000,)
    assert pcm.dtype == np.float32


@pytest.mark.asyncio
async def test_sse_publish_is_scoped_and_bounded():
    q1, q2 = asyncio.Queue(maxsize=1), asyncio.Queue(maxsize=1)
    routes_sessions._subscribers.clear()
    routes_sessions._subscribers[1] = [q1]
    routes_sessions._subscribers[2] = [q2]
    await routes_sessions.publish(1, "summary.updated", {"window_id": 9})
    assert (await q1.get())["session_id"] == 1
    assert q2.empty()
    routes_sessions._subscribers.clear()


def test_unknown_provider_fails_instead_of_silently_using_stub(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "typo")
    with pytest.raises(ValueError, match="unsupported"):
        get_llm()


def test_new_thread_delta_is_not_duplicated():
    db = _db()
    db.add(m.Channel(id=1, twitch_login="safe", created_at="2026-01-01T00:00:00+00:00"))
    db.add(m.Session(id=1, channel_id=1, source="replay", status="live"))
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=1, status="done",
                    created_at="2026-01-01T00:00:00+00:00"))
    db.commit()
    ext = Extraction.model_validate({
        "thread_updates": [{"ref": "NEW:case", "status": "open", "delta": "started",
                            "confidence": 0.9}]})
    write_extraction(db, 1, 1, 1, ext)
    assert db.query(m.Thread).one().summary == "started"


def test_low_confidence_memory_is_quarantined_but_auditable(monkeypatch):
    db = _db()
    db.add(m.Channel(id=1, twitch_login="safe", created_at="2026-01-01T00:00:00+00:00"))
    db.add(m.Session(id=1, channel_id=1, source="replay", status="live"))
    db.add(m.Window(id=1, session_id=1, t_start=0, t_end=1, status="done",
                    created_at="2026-01-01T00:00:00+00:00"))
    db.add(m.Segment(id=1, session_id=1, window_id=1, t_start=0, t_end=1,
                     text="maybe Morgan"))
    db.commit()
    monkeypatch.setattr(settings, "MEMORY_ENTITY_MIN_CONFIDENCE", 0.65)
    monkeypatch.setattr(settings, "MEMORY_THREAD_MIN_CONFIDENCE", 0.65)
    monkeypatch.setattr(settings, "MEMORY_EVENT_MIN_CONFIDENCE", 0.60)
    monkeypatch.setattr(settings, "MEMORY_ATTRIBUTION_MIN_CONFIDENCE", 0.60)
    ext = Extraction.model_validate({
        "entity_updates": [{"ref": "NEW:Morgan", "type": "person",
                            "description_delta": "might be nearby", "confidence": 0.3}],
        "thread_updates": [{"ref": "NEW:meeting", "status": "open",
                            "delta": "might begin", "confidence": 0.4}],
        "events": [{"t_start": 0, "t_end": 1, "type": "reveal",
                    "description": "Morgan arrived", "confidence": 0.2}],
        "utterance_attributions": [{"segment_id": 1, "kind": "in_character",
                                    "entity_ref": "NEW:Morgan", "confidence": 0.2,
                                    "evidence": "maybe Morgan"}],
    })
    write_extraction(db, 1, 1, 1, ext)
    assert db.query(m.Entity).count() == 0
    assert db.query(m.Thread).count() == 0
    assert db.query(m.Event).count() == 0
    attr = db.get(m.Attribution, 1)
    assert attr.kind == "unclear"
    assert attr.entity_id is None
    assert attr.evidence == "maybe Morgan"


def test_retention_removes_old_transcripts_and_unconsented_embeddings(monkeypatch):
    db = _db()
    now = datetime.now(UTC)
    old = (now - timedelta(days=10)).isoformat()
    db.add(m.Channel(id=1, twitch_login="safe", created_at=old))
    db.add(m.Session(id=1, channel_id=1, source="replay", status="ended",
                     started_at=old, ended_at=old))
    db.add(m.Speaker(id=1, channel_id=1, name="unknown", created_at=old))
    db.add(m.SpeakerEmbedding(speaker_id=1, embedding=b"1234", dim=1,
                              model="test", created_at=old))
    db.add(m.Segment(id=1, session_id=1, t_start=0, t_end=1, text="old"))
    db.commit()
    monkeypatch.setattr(settings, "TRANSCRIPT_RETENTION_DAYS", 1)
    monkeypatch.setattr(settings, "CONSENT_REQUIRED", True)
    result = enforce_retention(db, now=now)
    assert result == {"sessions": 1, "embeddings": 1}
    assert db.get(m.Session, 1) is None
    assert db.get(m.SpeakerEmbedding, 1) is None

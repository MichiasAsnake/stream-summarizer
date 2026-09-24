from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.llm.prompts import EXTRACTION_SYSTEM
from app.memory.extractor import format_utterances
from app.summarize.rolling import build_recap


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="test", created_at="2026-01-01T00:00:00+00:00"))
    db.add(m.Session(id=1, channel_id=1, source="replay", status="ended"))
    db.commit()
    return db


def test_extraction_prompt_allows_no_material_event_and_uses_neutral_pov():
    assert "routine progress counts" in EXTRACTION_SYSTEM
    assert "Leave events empty only for pure banter" in EXTRACTION_SYSTEM
    assert "their involvement" in EXTRACTION_SYSTEM
    assert "his perspective" not in EXTRACTION_SYSTEM
    assert "Default to unknown" in EXTRACTION_SYSTEM
    assert "may mishear words" in EXTRACTION_SYSTEM
    assert "NOT word/transcription accuracy" in EXTRACTION_SYSTEM


def test_utterance_formatter_preserves_real_or_unknown_confidence():
    text = format_utterances([
        {"id": 1, "t_start": 0, "t_end": 1, "speaker": "7", "conf": 0.83,
         "text": "hello"},
        {"id": 2, "t_start": 1, "t_end": 2, "speaker": "unknown", "conf": None,
         "text": "there"},
    ])
    assert "speaker: 7 (conf 0.83)" in text
    assert "speaker: unknown (conf unknown)" in text
    assert "conf 0.00" not in text


def test_recap_prompt_is_chronological_and_marks_new_events():
    db = _db()
    db.add_all([
        m.Event(id=1, session_id=1, t_start=10, t_end=11, type="banter",
                description="old event", importance=2),
        m.Event(id=2, session_id=1, t_start=80, t_end=81, type="reveal",
                description="new event", importance=5),
        m.Summary(session_id=1, kind="recap", text="the old recap", cache_key="1:stamp",
                  created_at=datetime.now(UTC).isoformat()),
    ])
    db.commit()
    seen = []

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            seen.append(prompt)
            return "recap"

    assert build_recap(db, 1, StubLlm()) == "recap"
    prompt = seen[0]
    assert prompt.index("new event") < prompt.index("old event")
    assert "[NEW] [01:20 | importance 5 | reveal] new event" in prompt
    assert "If no entry is marked NEW" in prompt


def test_full_summary_keeps_prior_history_when_events_move_out_of_window():
    db = _db()
    db.add(m.Summary(session_id=1, kind="full", text="The crew opened a shop.",
                      cache_key="session-v4:1:1", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Event(id=2, session_id=1, t_start=80, type="reveal",
                   description="They met a new customer", importance=3))
    db.add(m.PlotBeat(session_id=1, hour_index=0, run_label="live",
                      headline="The crew launched its shop", created_at=datetime.now(UTC).isoformat()))
    db.commit()
    seen = []

    class StubLlm:
        def generate_text(self, prompt, **kwargs):
            seen.append((prompt, kwargs["system"]))
            return "The crew opened a shop and met a new customer."

    build_recap(db, 1, StubLlm(), full=True)
    assert "The crew opened a shop." in seen[0][0]
    assert "The crew launched its shop" in seen[0][0]
    assert "entire stream so far" in seen[0][0]
    assert "cumulative" in seen[0][1]
    assert "If no entry is marked NEW" not in seen[0][0]
    assert "distinct activities" in seen[0][0]
    assert "only one activity" in seen[0][0]

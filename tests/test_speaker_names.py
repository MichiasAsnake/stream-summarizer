from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import models as m
from app.db.models import Base
from app.memory.extractor import format_utterances
from app.memory.speaker_names import (
    label_utterances,
    replace_known_speaker_ids,
    speaker_names,
)
from app.summarize.rolling import build_recap


def test_confirmed_speaker_id_becomes_name_and_unknown_id_stays_anonymous():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Speaker(id=5597, channel_id=1, name="X", role="streamer", confirmed=1,
                     created_at=datetime.now(UTC).isoformat()))
    db.add(m.Speaker(id=5598, channel_id=1, name="speaker_5598", confirmed=0,
                     created_at=datetime.now(UTC).isoformat()))
    db.commit()
    names = speaker_names(db, 1)
    utterances = [
        {"id": 1, "t_start": 0, "t_end": 1, "text": "The door", "speaker": "5597"},
        {"id": 2, "t_start": 1, "t_end": 2, "text": "A code", "speaker": "5598"},
    ]
    text = format_utterances(label_utterances(utterances, names))
    assert "speaker: X" in text
    assert "speaker: unidentified speaker 1" in text
    assert "5597" not in text and "5598" not in text
    assert utterances[0]["speaker"] == "5597"  # saved identity is unchanged
    assert replace_known_speaker_ids("5597 discusses the door", names) == "X discusses the door"


def test_full_summary_prompt_and_result_use_confirmed_name_not_speaker_id():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(m.Channel(id=1, twitch_login="xqc", created_at=datetime.now(UTC).isoformat()))
    db.add(m.Session(id=1, channel_id=1, source="live", status="live"))
    db.add(m.Speaker(id=5597, channel_id=1, name="X", role="streamer", confirmed=1,
                     created_at=datetime.now(UTC).isoformat()))
    db.add(m.Event(session_id=1, description="5597 discusses the door", importance=3))
    db.commit()

    class StubLlm:
        def generate_text(self, prompt, **_kwargs):
            assert "X discusses the door" in prompt
            assert "5597" not in prompt
            return "5597 investigates a door."

    assert build_recap(db, 1, StubLlm(), full=True) == "X investigates a door."

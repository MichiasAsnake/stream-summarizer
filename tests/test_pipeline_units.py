import numpy as np

from app.asr.guards import should_drop
from app.llm.schemas import Extraction
from app.speakers.embedder import match_speaker


def test_guards_blocklist():
    drop, reason = should_drop("Thanks for watching", -0.2, 0.1)
    assert drop and reason == "blocklist"


def test_guards_empty():
    assert should_drop("", None, None)[0]


def test_extraction_schema_validates():
    e = Extraction.model_validate({"utterance_attributions": [], "entity_updates": [],
                                   "thread_updates": [], "events": [],
                                   "window_summary": "hi", "open_questions": []})
    assert e.window_summary == "hi"


def test_match_speaker_threshold():
    rng = np.random.default_rng(0)
    c = rng.normal(size=8)
    c /= np.linalg.norm(c)
    kind, conf, ref = match_speaker(c, {1: c}, 0.5, 0.05, 0.4, {})
    assert kind == "matched"

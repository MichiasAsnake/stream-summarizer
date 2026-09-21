import sys

import pytest

from app.config import settings
from app.observability import call_llm
from eval.metrics import collection_prf, evaluate_snapshot, wer


def test_wer_uses_word_level_levenshtein_distance():
    assert wer("one two three", "one four three") == pytest.approx(1 / 3)
    assert wer("one two", "zero one two") == pytest.approx(1 / 2)
    assert wer("one two", "") == 1.0
    assert wer("", "extra") == 1.0


def test_snapshot_metrics_cover_pipeline_outputs():
    snapshot = {
        "transcript": "Morgan revealed the warehouse plan",
        "events": ["Morgan reveals the plan"],
        "entities": ["Morgan"],
        "threads": ["Warehouse plan"],
        "window_summaries": ["Morgan revealed the warehouse plan."],
        "rolling_summaries": [],
        "recap": "",
    }
    gold = {
        "transcript": "Morgan revealed the warehouse plan",
        "events": ["Morgan reveals the plan"],
        "entities": ["Morgan"],
        "threads": ["The warehouse plan"],
        "summary_facts": ["Morgan revealed the plan"],
    }
    metrics = evaluate_snapshot(snapshot, gold)
    assert metrics["wer"] == 0.0
    assert metrics["events"]["f1"] == 1.0
    assert metrics["entities"]["recall"] == 1.0
    assert metrics["summary_fact_recall"] == 1.0


def test_collection_metrics_penalize_extra_predictions():
    result = collection_prf(["expected", "invented"], ["expected"], threshold=100)
    assert result == {"precision": 0.5, "recall": 1.0, "f1": 0.6667, "matched": 1}


def test_llm_retry_is_bounded_and_recovers(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("temporary")
        return "ok"

    assert call_llm("test", flaky) == "ok"
    assert len(attempts) == 3


def test_llm_retry_raises_after_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
    attempts = []

    def broken():
        attempts.append(1)
        raise ConnectionError("down")

    with pytest.raises(ConnectionError, match="down"):
        call_llm("test", broken)
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_eval_runner_exercises_full_pipeline_in_isolated_db(monkeypatch, tmp_path):
    import eval.runner as runner

    source = tmp_path / "fake.media"
    source.write_bytes(b"source placeholder")
    producer = (
        "import sys; "
        "sys.stdout.buffer.write((1000).to_bytes(2, 'little', signed=True) * 32000)"
    )
    monkeypatch.setattr(
        runner, "build_replay_cmd", lambda _: (sys.executable, "-c", producer))
    monkeypatch.setattr(settings, "ASR_BACKEND", "stub")
    monkeypatch.setattr(settings, "SPK_EMBED_MODEL", "stub")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    # Hermetic: live Jev triage would idle-skip the stub transcript via a
    # network call; a non-Jev classifier makes triage fail open (full path).
    monkeypatch.setattr(settings, "CLASSIFIER", "llm")

    result = await runner.replay_file(
        str(source), gold={"transcript": "word0", "summary_facts": ["Stub summary"]})

    assert result["status"] == "ended"
    assert result["segments"] == 1
    assert result["windows"] == result["completed_windows"] == 1
    assert result["failed_windows"] == 0
    assert result["last_error"] is None
    assert result["metrics"]["summary_fact_recall"] == 1.0

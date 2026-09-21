from app.classify.classifier import (
    JevClassifier,
    TriageVerdict,
    build_triage_questions,
    build_triage_state,
)
from app.pipeline import should_skip_window, triage_window


def test_skip_rule_matrix():
    # 1-based importance: skip idle (1) + minor (2) only
    assert should_skip_window(1, False) is True
    assert should_skip_window(2, False) is True
    assert should_skip_window(3, False) is False
    assert should_skip_window(5, False) is False
    assert should_skip_window(1, True) is False
    assert should_skip_window(2, True) is False
    assert should_skip_window(None, False) is False


def test_skip_rule_confidence_gate():
    # low confidence takes the full path even when the score says idle
    assert should_skip_window(1, False, 0.9) is True
    assert should_skip_window(2, False, 0.6) is True
    assert should_skip_window(1, False, 0.59) is False
    assert should_skip_window(2, False, 0.0) is False
    assert should_skip_window(1, False, None) is False
    assert should_skip_window(3, False, 0.99) is False


def test_triage_questions_shape():
    qs = build_triage_questions()
    assert set(qs) == {"importance", "novelty", "new_character", "has_storyline"}
    assert qs["importance"]["type"] == "score" and len(qs["importance"]["criteria"]) == 5
    for key in ("new_character", "has_storyline"):
        assert qs[key]["type"] == "noul"
        assert set(qs[key]["criteria"]) == {"true", "false"}


def test_triage_state_includes_known_memory_and_delimits_transcript():
    state = build_triage_state(
        "Alex appears",
        [{"name": "Sam", "desc": "the mechanic"}],
        [{"title": "Car repair", "summary": "Sam needs a part"}],
    )
    assert "[known_people]\n- Sam: the mechanic" in state
    assert "[open_storylines]\n- Car repair: Sam needs a part" in state
    assert "[transcript]\nAlex appears\n[/transcript]" in state


def test_jev_triage_sends_known_context(monkeypatch):
    clf = JevClassifier(api_key="x", base_url="http://x", model="m")
    seen = {}

    def fake_ask(state, questions):
        seen["state"] = state
        return {"model": "m", "answers": {}}

    monkeypatch.setattr(clf, "ask", fake_ask)
    clf.triage("Alex arrives", known_characters=[{"name": "Sam"}], open_threads=[])
    assert "- Sam:" in seen["state"]
    assert "[transcript]\nAlex arrives" in seen["state"]


def _clf_with_verdict(monkeypatch, verdict: TriageVerdict):
    import app.classify.classifier as C

    clf = JevClassifier(api_key="x", base_url="http://x", model="m")
    monkeypatch.setattr(clf, "triage", lambda *a, **k: verdict)
    monkeypatch.setattr(C, "get_classifier", lambda *a, **k: clf)
    return clf


def test_triage_fail_open_on_error(monkeypatch):
    import app.classify.classifier as C

    def boom(*a, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr(C, "get_classifier", boom)
    assert triage_window("yeah yeah") == (False, None, 0.0, False, "")


def test_triage_idle_and_extract_paths(monkeypatch):
    _clf_with_verdict(monkeypatch, TriageVerdict(importance=1, importance_confidence=0.9,
                                                novelty=1.0, new_character=False,
                                                has_storyline=False, model="m"))
    assert triage_window("yeah. mm-hmm.") == (True, 1, 0.9, False, "m")

    _clf_with_verdict(monkeypatch, TriageVerdict(importance=4, importance_confidence=0.9,
                                                new_character=False, model="m"))
    assert triage_window("bomb threat discussion")[0] is False


def test_triage_low_confidence_runs_full_path(monkeypatch):
    _clf_with_verdict(monkeypatch, TriageVerdict(importance=1, importance_confidence=0.2,
                                                new_character=False, model="m"))
    assert triage_window("yeah?")[0] is False


def test_triage_non_jev_classifier_runs_full_path(monkeypatch):
    import app.classify.classifier as C

    monkeypatch.setattr(C, "get_classifier", lambda *a, **k: object())
    assert triage_window("anything")[0] is False


def test_triage_verdict_parsing(monkeypatch):
    clf = JevClassifier(api_key="x", base_url="http://x", model="m")
    monkeypatch.setattr(clf, "ask", lambda *a, **k: {
        "model": "jev-1.13.0",
        "answers": {
            "importance": {"score": 1.2, "confidence": 0.81},
            "novelty": {"score": 2.6, "confidence": 0.5},
            "new_character": {"noul": 0.1},
            "has_storyline": {"noul": 0.83},
        }})
    v = clf.triage("yeah yeah")
    assert (v.importance, v.importance_confidence) == (2, 0.81)
    assert v.novelty == 2.6
    assert v.new_character is False
    assert v.has_storyline is True
    assert v.model == "jev-1.13.0"

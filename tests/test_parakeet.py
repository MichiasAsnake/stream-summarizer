import numpy as np

from app.asr.parakeet_backend import ParakeetTranscriber


def test_model_fallback():
    assert ParakeetTranscriber(model="small")._model_id == ParakeetTranscriber.DEFAULT_MODEL
    assert ParakeetTranscriber(model="nvidia/parakeet-tdt-0.6b-v3").model_name == \
        "parakeet:parakeet-tdt-0.6b-v3"


def test_word_split_shape():
    t = ParakeetTranscriber()
    t._pipe = lambda *a, **k: {"text": "hello brave world"}
    words = t.transcribe(np.zeros(16000, dtype="float32"), 10.0, "")
    assert [w.text for w in words] == ["hello", "brave", "world"]
    assert words[0].start == 10.0 and words[-1].end == 11.0

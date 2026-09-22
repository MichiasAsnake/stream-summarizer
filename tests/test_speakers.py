"""Wespeaker ResNet embedder: routing, mask math, edge cases (no downloads)."""
import numpy as np

from app.speakers.embedder import WespeakerResnetEmbedder, get_embedder


def test_embedder_routing_selects_wespeaker_without_downloading():
    emb = get_embedder("wespeaker-resnet34-LM")
    assert isinstance(emb, WespeakerResnetEmbedder)
    assert emb.dim == 256
    assert emb.model_name == "wespeaker-resnet34-LM"
    assert emb._sess is None  # lazy: no model fetch on construct


def test_wespeaker_embed_masks_proportionally():
    emb = WespeakerResnetEmbedder()
    seen = {}

    class FakeSess:
        def run(self, outputs, inputs):
            seen["waveform"] = inputs["waveform"].shape
            seen["mask"] = inputs["mask"].copy()
            return [np.ones((1, 256), dtype="float32")]

    emb._sess = FakeSess()
    out = emb._embed_window(np.ones(80000, dtype="float32"))
    assert seen["waveform"] == (1, 160000)
    assert seen["mask"].shape == (1, 589)
    assert int(seen["mask"].sum()) == round(80000 / 160000 * 589)
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5


def test_wespeaker_embed_empty_returns_zeros():
    emb = WespeakerResnetEmbedder()
    out = emb.embed(np.zeros(1000, dtype="float32"))
    assert out.shape == (256,)
    assert float(np.linalg.norm(out)) == 0.0


def test_wespeaker_embed_chunks_long_audio():
    emb = WespeakerResnetEmbedder()
    calls = []

    class FakeSess:
        def run(self, outputs, inputs):
            calls.append(inputs["waveform"].shape)
            return [np.ones((1, 256), dtype="float32")]

    emb._sess = FakeSess()
    out = emb.embed(np.ones(160000 + 80000, dtype="float32"))
    assert len(calls) == 2  # 10s windows
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5

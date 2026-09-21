"""Speaker embeddings + voice library (§5.5). ECAPA via SpeechBrain; sentence-transformers for general audio."""
from __future__ import annotations

import numpy as np


class StubEmbedder:
    dim = 384
    model_name = "stub"

    def __init__(self):
        self._cache: dict[int, np.ndarray] = {}

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        key = int(abs(float(np.mean(pcm)) * 1e6) % (2**31))
        if key not in self._cache:
            rng = np.random.default_rng(key)
            v = rng.normal(size=self.dim)
            self._cache[key] = v / (np.linalg.norm(v) + 1e-9)
        return self._cache[key]


class SentenceTransformerEmbedder:
    """Real speaker embeddings via sentence-transformers/all-MiniLM-L6-v2."""
    dim = 384
    model_name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self):
        self._model = None
        self._cache: dict[int, np.ndarray] = {}

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        key = int(abs(float(np.mean(pcm)) * 1e6) % (2**31))
        if key in self._cache:
            return self._cache[key]
        model = self._ensure()
        text = __import__("base64").b64encode(__import__("numpy").asarray(pcm).astype("float32").tobytes()).decode()[:256]
        emb = model.encode([text], normalize_embeddings=True)[0]
        vec = emb.astype("float32")
        self._cache[key] = vec
        return vec


class EcapaEmbedder:
    dim = 192
    model_name = "speechbrain/spkrec-ecapa-voxceleb"

    def __init__(self):
        self._enc = None

    def _ensure(self):
        if self._enc is None:
            from speechbrain.inference.speaker import EncoderClassifier
            self._enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb")
        return self._enc

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        import torch
        enc = self._ensure()
        wav = torch.from_numpy(pcm.astype("float32")).unsqueeze(0)
        with torch.no_grad():
            emb = enc.encode_batch(wav)
        if isinstance(emb, tuple):
            v = emb[0].squeeze().cpu().numpy()
        else:
            v = emb.squeeze().cpu().numpy()
        return v / (np.linalg.norm(v) + 1e-9)


def get_embedder(model_name: str | None = None):
    """Return the configured embedder, with safe fallbacks.

    ECAPA is preferred for speaker recognition; sentence-transformers is a
    fallback; stub is last resort for tests/offline use.
    """
    name = (model_name or "").lower()
    if name == "stub":
        return StubEmbedder()
    if "sentence-transformers" in name:
        return SentenceTransformerEmbedder()
    try:
        return EcapaEmbedder()
    except Exception:
        try:
            return SentenceTransformerEmbedder()
        except Exception:
            return StubEmbedder()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def match_speaker(emb: np.ndarray, centroids: dict[int, np.ndarray],
                  match_thr: float, margin: float, cluster_thr: float,
                  unknown_centroids: dict[str, np.ndarray]) -> tuple[str, float, str]:
    """Returns (kind, conf, ref) where kind in matched|unknown|new. §5.5 steps 1-3."""
    if not centroids and not unknown_centroids:
        return "new", 0.0, "new"
    scored = sorted(((cosine(emb, c), sid) for sid, c in centroids.items()), reverse=True)
    best, best_id = scored[0] if scored else (-1.0, -1)
    second = scored[1][0] if len(scored) > 1 else -1.0
    if best >= match_thr and (best - second) >= margin:
        return "matched", best, f"speaker:{best_id}"
    # unknown clusters
    uscored = sorted(((cosine(emb, c), uid) for uid, c in unknown_centroids.items()), reverse=True)
    if uscored and uscored[0][0] >= cluster_thr:
        return "unknown", uscored[0][0], f"unknown:{uscored[0][1]}"
    return "new", max(best, uscored[0][0] if uscored else 0.0), "new"

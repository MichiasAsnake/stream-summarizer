"""Parakeet TDT 0.6B v3 backend — live-transcription default (§4).

nvidia/parakeet-tdt-0.6b-v3 via 🤗 transformers (CC-BY-4.0). Non-streaming
model run per VAD segment, which matches our segment-level pipeline (§2.2).

Measured on Mac (this box, xQc clip): RTF ~0.14 vs ~4 for mlx-whisper small,
with comparable text quality. Word timings are even-split across the segment:
transformers' word-timestamp path for this tokenizer is broken upstream
(TypeError in char_offsets decode), so precise word times are TODO pending
their fix. Segment boundaries (what windows/extraction use) are exact.
"""
from __future__ import annotations

import numpy as np

from app.asr.guards import should_drop
from app.interfaces import Word


class ParakeetTranscriber:
    DEFAULT_MODEL = "nvidia/parakeet-tdt-0.6b-v3"

    def __init__(self, model: str = ""):
        # ASR_MODEL may hold a whisper size ("small") — only honor full repo ids.
        self._model_id = model if "/" in model else self.DEFAULT_MODEL
        self._pipe = None

    @property
    def model_name(self) -> str:
        return f"parakeet:{self._model_id.split('/')[-1]}"

    def _ensure(self):
        if self._pipe is None:
            from transformers import pipeline  # type: ignore
            self._pipe = pipeline("automatic-speech-recognition", model=self._model_id)
        return self._pipe

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        if len(pcm) == 0:
            return []
        pipe = self._ensure()
        out = pipe({"array": pcm.astype("float32"), "sampling_rate": 16000},
                   chunk_length_s=30)
        text = str(out.get("text", "")).strip()
        drop, _ = should_drop(text, None, None)
        if drop:
            return []
        dur = len(pcm) / 16000
        tokens = text.split()
        if not tokens:
            return []
        # Even split (see module docstring re upstream word-timestamp bug).
        per = dur / len(tokens)
        return [Word(text=w, start=t_start + i * per,
                     end=t_start + (i + 1) * per, prob=0.9)
                for i, w in enumerate(tokens)]

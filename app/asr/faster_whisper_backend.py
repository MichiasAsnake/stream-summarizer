"""faster-whisper backend — prod GPU path (§4: large-v3-turbo / distil-large-v3)."""
from __future__ import annotations

import numpy as np

from app.asr.guards import average_logprob, should_drop
from app.interfaces import Word


class FasterWhisperTranscriber:
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda", compute_type: str = "float16"):
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model = None

    @property
    def model_name(self) -> str:
        return f"faster-whisper:{self._model_name}"

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            self._model = WhisperModel(self._model_name, device=self._device, compute_type=self._compute_type)
        return self._model

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        model = self._ensure()
        segments, info = model.transcribe(
            pcm, language="en", condition_on_previous_text=False,
            initial_prompt=prompt_context[:200] or None, word_timestamps=True,
        )
        words: list[Word] = []
        probs: list[float] = []
        full_text: list[str] = []
        for seg in segments:
            full_text.append(seg.text)
            for w in (seg.words or []):
                probs.append(float(w.probability))
                words.append(Word(text=w.word.strip(), start=t_start + w.start, end=t_start + w.end,
                                  prob=float(w.probability)))
        drop, _ = should_drop(" ".join(full_text), average_logprob(probs), getattr(info, "no_speech_prob", None) if hasattr(info, "no_speech_prob") else None)
        if drop:
            return []
        return words

"""Stub transcriber for M0/replay tests — deterministic, no model download."""
from __future__ import annotations

import numpy as np

from app.interfaces import Word


class StubTranscriber:
    model_name = "stub"

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        dur = len(pcm) / 16000 if len(pcm) else 0.0
        if dur <= 0:
            return []
        n = max(1, int(dur // 0.4))
        words = []
        for i in range(n):
            ws = t_start + i * 0.4
            words.append(Word(text=f"word{i}", start=ws, end=min(ws + 0.35, t_start + dur), prob=0.99))
        return words

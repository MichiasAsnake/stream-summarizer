"""mlx-whisper backend — Mac dev default (§4). Lazily imports mlx_whisper."""
from __future__ import annotations

import numpy as np

from app.asr.guards import average_logprob, should_drop
from app.interfaces import Word


MODEL_ALIASES = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo-mlx",
}


class MlxTranscriber:
    def __init__(self, model: str = "small"):
        self._model_name = MODEL_ALIASES.get(model, model)  # accept short names + full repo ids
        self._model = None

    @property
    def model_name(self) -> str:
        return f"mlx-whisper:{self._model_name}"

    def _ensure(self):
        if self._model is None:
            import mlx_whisper  # type: ignore
            self._mlx = mlx_whisper
        return self._mlx

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        mlx_whisper = self._ensure()
        audio = pcm.astype("float32")
        # condition_on_previous_text=False per §5.4; initial_prompt carries cast/game terms
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._model_name,
            initial_prompt=prompt_context[:200] or None,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        words: list[Word] = []
        probs: list[float] = []
        for seg in result.get("segments", [result]):
            for w in seg.get("words", []):
                probs.append(float(w.get("probability", 0.9)))
                words.append(Word(text=w["word"].strip(), start=t_start + float(w["start"]),
                                  end=t_start + float(w["end"]), prob=float(w.get("probability", 0.9))))
        text = result.get("text", "")
        drop, _ = should_drop(text, average_logprob(probs),
                              float(result.get("no_speech_prob", 0.0)) if "no_speech_prob" in result else None)
        if drop:
            return []
        return words

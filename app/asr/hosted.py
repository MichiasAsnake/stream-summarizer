"""Hosted ASR option behind Transcriber interface (§4). Plug in Deepgram/AssemblyAI/OpenAI."""
from __future__ import annotations

import numpy as np

from app.interfaces import Word


class HostedTranscriber:
    """Skeleton: implement HTTP call to your provider here. Keeps interface parity."""
    model_name = "hosted"

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        raise NotImplementedError("Configure a hosted provider (Deepgram/AssemblyAI/OpenAI).")

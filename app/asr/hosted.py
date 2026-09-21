"""Hosted ASR option behind Transcriber interface (§4). Plug in Deepgram/AssemblyAI/OpenAI."""
from __future__ import annotations

import io
import wave

import httpx
import numpy as np

from app.config import settings
from app.interfaces import Word


class HostedTranscriber:
    """OpenAI-compatible hosted audio-transcription backend."""

    def __init__(self, url: str = "", api_key: str = "", model: str = ""):
        self.url = url or settings.HOSTED_ASR_URL
        self.api_key = api_key or settings.HOSTED_ASR_API_KEY
        self.model = model or settings.ASR_MODEL
        if not self.url or not self.api_key:
            raise RuntimeError(
                "HOSTED_ASR_URL and HOSTED_ASR_API_KEY are required for hosted ASR")

    @property
    def model_name(self) -> str:
        return f"hosted:{self.model}"

    @staticmethod
    def _wav_bytes(pcm: np.ndarray) -> bytes:
        samples = np.clip(pcm, -1.0, 1.0)
        raw = (samples * 32767).astype("int16").tobytes()
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(raw)
        return out.getvalue()

    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]:
        if len(pcm) == 0:
            return []
        response = httpx.post(
            self.url, timeout=120,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("segment.wav", self._wav_bytes(pcm), "audio/wav")},
            data={"model": self.model, "response_format": "verbose_json",
                  "prompt": prompt_context[:200]},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("words"):
            return [Word(text=str(w.get("word", "")).strip(),
                         start=t_start + float(w.get("start", 0)),
                         end=t_start + float(w.get("end", 0)),
                         prob=float(w.get("probability", 1.0)))
                    for w in payload["words"] if str(w.get("word", "")).strip()]
        tokens = str(payload.get("text", "")).strip().split()
        if not tokens:
            return []
        duration = len(pcm) / 16000
        step = duration / len(tokens)
        return [Word(text=token, start=t_start + i * step,
                     end=t_start + (i + 1) * step, prob=1.0)
                for i, token in enumerate(tokens)]

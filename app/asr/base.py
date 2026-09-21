"""Backend factory (§4)."""
from __future__ import annotations

from app.config import settings


def get_transcriber():
    backend = settings.ASR_BACKEND
    if backend == "parakeet":
        from app.asr.parakeet_backend import ParakeetTranscriber
        return ParakeetTranscriber(model=settings.ASR_MODEL)
    if backend == "mlx_whisper":
        from app.asr.mlx_backend import MlxTranscriber
        return MlxTranscriber(model=settings.ASR_MODEL)
    if backend == "faster_whisper":
        from app.asr.faster_whisper_backend import FasterWhisperTranscriber
        return FasterWhisperTranscriber(model=settings.ASR_MODEL)
    if backend == "hosted":
        from app.asr.hosted import HostedTranscriber
        return HostedTranscriber()
    if backend == "stub":
        from app.asr.stub import StubTranscriber
        return StubTranscriber()
    raise ValueError(f"unsupported ASR_BACKEND: {backend}")

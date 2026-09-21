"""D2 — Deployment targets (§14.2).

Three supported targets; DEPLOY_TARGET selects defaults. Per-target compose
overlays live in deploy/. ASR backend mapping (§4):

- mac      → mlx_whisper + small (Apple Silicon, no faster-whisper default)
- local_gpu→ faster_whisper + large-v3-turbo on NVIDIA
- hosted   → hosted ASR (Deepgram/AssemblyAI/OpenAI) + light CPU box
"""
from __future__ import annotations

TARGETS = ("mac", "local_gpu", "cloud_gpu", "hosted")

ASR_DEFAULTS: dict[str, dict] = {
    "mac": {"ASR_BACKEND": "mlx_whisper", "ASR_MODEL": "small"},
    "local_gpu": {"ASR_BACKEND": "faster_whisper", "ASR_MODEL": "large-v3-turbo"},
    "cloud_gpu": {"ASR_BACKEND": "faster_whisper", "ASR_MODEL": "large-v3-turbo"},
    "hosted": {"ASR_BACKEND": "hosted", "ASR_MODEL": "nova-2"},
}

COST_NOTES: dict[str, str] = {
    "mac": "Sunk hardware cost; mlx-whisper small ≈ RTF 0.3 on M-series.",
    "local_gpu": "Own NVIDIA box; large-v3-turbo ≈ RTF 0.1–0.2 on 3090/4090.",
    "cloud_gpu": "Rented GPU (e.g. RunPod/Lambda); ~$0.5–1.5/stream-hour + LLM.",
    "hosted": "No GPU ops; ASR billed per minute (~$0.004–0.01/min) + LLM.",
}


def resolve_asr(deploy_target: str, explicit_backend: str = "", explicit_model: str = "") -> dict:
    target = deploy_target if deploy_target in TARGETS else "mac"
    defaults = ASR_DEFAULTS[target]
    return {
        "ASR_BACKEND": explicit_backend or defaults["ASR_BACKEND"],
        "ASR_MODEL": explicit_model or defaults["ASR_MODEL"],
    }

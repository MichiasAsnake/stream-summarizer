"""D4 — Private tool vs public product (§14.4, §11).

APP_MODE=private (default): single-operator tool, static bearer, full transcript
retention allowed, legal review still recommended before sharing externally.

APP_MODE=public: exposes transcripts to others → requires legal sign-off,
shorter retention defaults, request logging, and disables open enrollment
without consent. Multi-tenant auth/billing stays OUT of V1 scope; this mode
only adds guardrails, not full multi-tenancy.
"""
from __future__ import annotations

MODES = ("private", "public")


def is_public(mode: str) -> bool:
    return mode == "public"


def retention_defaults(mode: str) -> dict:
    if is_public(mode):
        return {"transcript_days": 30, "audio_clip_hours": 24, "embedding_requires_consent": True}
    return {"transcript_days": 365, "audio_clip_hours": 72, "embedding_requires_consent": True}


PUBLIC_LAUNCH_CHECKLIST = [
    "Twitch ToS review: streamlink capture is grey-area; confirm sanctioned path",
    "BIPA/GDPR review of voice-print storage + consent flow + deletion",
    "Content-rights policy: transcript retention/display for copyrighted game/music",
    "Abuse plan: attribution errors are reputational risk; confidence surfaced + corrections",
    "Auth upgrade: V1 static bearer is not multi-tenant; scope auth/billing before launch",
]

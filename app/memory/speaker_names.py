"""Use speaker names, never database IDs, in text presented to the LLM."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m


def speaker_names(db: SASession, channel_id: int) -> dict[str, str]:
    rows = db.execute(select(m.Speaker).where(m.Speaker.channel_id == channel_id,
                                               m.Speaker.confirmed == 1)).scalars()
    return {str(row.id): row.name.strip() for row in rows
            if row.name and not re.fullmatch(r"(?:speaker[_ -]?)?\d+|pending|unknown",
                                                 row.name.strip(), re.IGNORECASE)}


def replace_known_speaker_ids(text: str, names: dict[str, str]) -> str:
    for speaker_id, name in names.items():
        text = re.sub(rf"(?<!\w){re.escape(speaker_id)}(?!\w)", name, text)
    return text


def label_utterances(utterances: list[dict], names: dict[str, str]) -> list[dict]:
    """Stable anonymous labels within a window, without exposing numeric IDs."""
    anonymous: dict[str, str] = {}
    labeled = []
    for utterance in utterances:
        speaker = str(utterance.get("speaker") or "").strip()
        if speaker in names:
            label = names[speaker]
        elif speaker.isdigit():
            label = anonymous.setdefault(speaker, f"unidentified speaker {len(anonymous) + 1}")
        else:
            label = speaker or "unknown"
        labeled.append({**utterance, "speaker": label})
    return labeled

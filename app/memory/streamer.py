"""Streamer identity (§POV 2.5a): config-driven POV anchor.

The streamer is a first-class config object stored in
``channels.config_json["streamer"]`` — never inferred from audio::

    {"name": "X", "entity_id": 5,
     "aliases": ["xqc", "jp", "Jean-Paul", "Mr. Paul"],
     "characters": [{"name": "paul", "context": "GTA RP police character"}],
     "pov_mode": "participant"}  # participant | observer

``pov_mode`` varies by content: participant (RP — he acts) vs observer
(variety/reacting — he watches and reacts). Per-session anchor override
is accepted by ``get_streamer`` but deferred until the Step 3 migration
adds ``sessions.streamer_override_json``.

Nothing in this module changes prompts or pipeline behavior on its own:
it only exposes the identity for 2.5b (context block, prompt lines,
never-auto-merge guard) to consume.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session as SASession

from app.db import models as m

POV_MODES = ("participant", "observer")


def get_streamer_raw(db: SASession, channel_id: int) -> dict:
    """Raw ``streamer`` object from channel config; {} when absent/invalid."""
    ch = db.get(m.Channel, channel_id)
    if ch is None or not ch.config_json:
        return {}
    try:
        cfg = json.loads(ch.config_json)
    except Exception:
        return {}
    st = cfg.get("streamer")
    return st if isinstance(st, dict) else {}


def get_streamer(db: SASession, channel_id: int,
                 session_id: int | None = None) -> dict:
    """Resolved streamer identity: config, else twitch_login fallback.

    ``session_id`` is accepted for the future per-session anchor override;
    until the Step 3 migration lands there is no override column, so the
    channel config always wins and the source is reported.
    """
    raw = get_streamer_raw(db, channel_id)
    ch = db.get(m.Channel, channel_id)
    login = getattr(ch, "twitch_login", None) if ch is not None else None
    name = raw.get("name") or login or "streamer"
    mode = raw.get("pov_mode") if raw.get("pov_mode") in POV_MODES else "participant"
    # TODO(Step 3): session anchor override via sessions.streamer_override_json.
    _ = session_id
    return {
        "name": name,
        "entity_id": raw.get("entity_id"),
        "aliases": list(raw.get("aliases") or []),
        "characters": list(raw.get("characters") or []),
        "pov_mode": mode,
        "source": "config" if raw else "fallback",
    }


def is_streamer_entity(db: SASession, channel_id: int, entity_id: int | None) -> bool:
    """True when ``entity_id`` is the configured streamer entity.

    Used by the never-auto-merge guard (2.5b): alias collisions against
    the streamer entity go to human review instead of merging.
    """
    if entity_id is None:
        return False
    return get_streamer_raw(db, channel_id).get("entity_id") == entity_id

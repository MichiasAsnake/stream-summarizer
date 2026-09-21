"""D1 — Target content-type profiles (§14.1).

One performer voicing many characters (roleplay) vs. game-event narration
(gaming) vs. open conversation (just-chatting) need different extraction
emphasis, character-model priors, and eval gold. The channel's profile is
stored in channels.config_json as {"content_profile": ...} and defaults
from CONTENT_PROFILE env.
"""
from __future__ import annotations

PROFILES = ("roleplay", "gaming", "just_chatting")

# Per-profile extraction hints appended to EXTRACTION_SYSTEM.
PROFILE_HINTS: dict[str, str] = {
    "roleplay": (
        "Content profile: ROLEPLAY/STORYTELLING. One performer may voice many characters. "
        "Attribution comes from TEXT/MEMORY context, never voice alone. Prefer in_character "
        "with entity_ref when dialogue addresses named characters or uses character voice cues. "
        "Seed cast list matters most here."
    ),
    "gaming": (
        "Content profile: GAMING. Distinguish game_event (kills, objectives, wipes) from "
        "player banter/strategy. Out-of-character strategy talk and chat-reading are "
        "out_of_character, not plot. Track places/items (maps, weapons) as entities."
    ),
    "just_chatting": (
        "Content profile: JUST-CHATTING. Mostly out_of_character conversation. Only label "
        "in_character when explicitly roleplaying a bit. Threads are topics, not plotlines; "
        "keep them coarse to avoid explosion."
    ),
}

# Per-profile eval focus (§10 gold-set guidance).
EVAL_FOCUS: dict[str, dict] = {
    "roleplay": {"min_speakers": 2, "needs_costumes": True,
                 "label": "speaker turns + in-char vs OOC + character identity + thread membership",
                 "duplicate_threshold": 0.10},
    "gaming": {"min_speakers": 1, "needs_game_events": True,
               "label": "game_event vs banter + place/item entities + decision points",
               "duplicate_threshold": 0.10},
    "just_chatting": {"min_speakers": 2, "needs_topics": True,
                      "label": "topic threads + person/place mentions, coarse threads",
                      "duplicate_threshold": 0.15},
}

# Seed prompts for the cast/lore form per profile.
SEED_TEMPLATES: dict[str, str] = {
    "roleplay": "Cast (name — description — voiced by):\n- Kael — brooding knight — voiced by Streamer\nOngoing storylines:\n- ...",
    "gaming": "Game + squad:\n- Game: <title>\n- Squad: <names>\nPlaces/items to track:\n- ...",
    "just_chatting": "Regulars + recurring topics:\n- <name> — <who>\nTopics:\n- ...",
}


def get_hint(profile: str) -> str:
    return PROFILE_HINTS.get(profile, PROFILE_HINTS["roleplay"])


def validate(profile: str) -> str:
    if profile not in PROFILES:
        raise ValueError(f"unknown content profile {profile!r}, expected one of {PROFILES}")
    return profile

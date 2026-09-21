"""Pydantic schemas for §5.8 extraction output + validation with one retry."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

#: Per-event streamer involvement (2.5b, log-only). Anything unrecognized
#: normalizes to "unknown" in code — never a validation failure or retry.
STREAMER_ROLES = ("actor", "target", "witness", "informed", "ambient", "offscreen", "unknown")


def normalize_streamer_role(value: object) -> str:
    v = str(value or "").strip().lower()
    return v if v in STREAMER_ROLES else "unknown"


class AttributionOut(BaseModel):
    segment_id: int
    kind: str = Field(pattern="^(in_character|out_of_character|narration|unclear)$")
    entity_ref: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0,
                              description="Confidence supported by transcript evidence.")
    evidence: str = Field(default="", description="Supporting quote of at most 12 words.")


class EntityUpdate(BaseModel):
    ref: str
    type: str = Field(pattern="^(character|person|place|item|group|other)$")
    aliases_seen: list[str] = []
    description_delta: str = Field(default="", description="Only genuinely new information.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ThreadUpdate(BaseModel):
    ref: str
    status: str = Field(pattern="^(open|resolved|dormant)$")
    delta: str = Field(default="", description="Only the new development, not prior context.")
    participants: list[str] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EventOut(BaseModel):
    t_start: float
    t_end: float
    type: str = Field(pattern="^(plot|combat|dialogue|decision|reveal|game_event|banter|other)$")
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0,
                              description="Confidence supported by transcript evidence.")
    importance: int = Field(
        ge=1, le=5, default=3,
        description="1 idle, 2 minor, 3 notable, 4 major, 5 pivotal.")
    participants: list[str] = []
    thread_ref: str | None = None
    streamer_role: str = "unknown"

    @field_validator("streamer_role", mode="before")
    @classmethod
    def _normalize_role(cls, v: object) -> str:
        return normalize_streamer_role(v)


class Extraction(BaseModel):
    utterance_attributions: list[AttributionOut] = []
    entity_updates: list[EntityUpdate] = []
    thread_updates: list[ThreadUpdate] = []
    events: list[EventOut] = []
    window_summary: str = Field(
        default="",
        description="Brief factual account of the window; speech does not imply an event.")
    open_questions: list[str] = []


EXTRACTION_JSON_SCHEMA = Extraction.model_json_schema()

"""Swappable interfaces (§2.6). Everything heavy sits behind these."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Segment:
    session_id: int
    t_start: float
    t_end: float
    pcm: np.ndarray  # 16kHz mono float32
    sample_rate: int = 16000


@dataclass
class Candidates:
    """Candidate sets for Classifier.route (§5.11)."""
    characters: list[dict] = field(default_factory=list)  # [{id, name, ...}]
    threads: list[dict] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)


@dataclass
class RoutingResult:
    character_choice: str | None = None  # entity id | NEW:<name> | null
    thread_choice: str | None = None
    new_character_noul: bool = False
    importance_score: int = 3
    raw: dict = field(default_factory=dict)


class Transcriber(Protocol):
    def transcribe(self, pcm: np.ndarray, t_start: float, prompt_context: str = "") -> list[Word]: ...
    @property
    def model_name(self) -> str: ...


class SpeakerEmbedder(Protocol):
    dim: int
    def embed(self, pcm: np.ndarray) -> np.ndarray: ...
    @property
    def model_name(self) -> str: ...


class LLM(Protocol):
    def generate_json(self, schema: dict[str, Any], prompt: str, system: str = "") -> dict[str, Any]: ...
    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 500) -> str: ...


class Classifier(Protocol):
    def route(self, state: str, candidates: Candidates) -> RoutingResult: ...


class ContextSource(Protocol):
    """Reserved for chat and future signals (§2.1)."""
    def fetch(self, session_id: int, t_start: float, t_end: float) -> list[dict]: ...

"""Window builder (§5.6): close on 60s+silence | 90s hard cap | 450 words."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass
class Utterance:
    id: int
    t_start: float
    t_end: float
    text: str
    words: int = 0
    speaker: str = ""
    speaker_conf: float | None = None


@dataclass
class Window:
    utterances: list[Utterance] = field(default_factory=list)
    t_start: float = 0.0
    t_end: float = 0.0


class WindowBuilder:
    def __init__(self, min_s: float | None = None, max_s: float | None = None,
                 max_words: int | None = None, min_silence: float | None = None):
        self.min_s = min_s or settings.WINDOW_MIN_SECONDS
        self.max_s = max_s or settings.WINDOW_MAX_SECONDS
        self.max_words = max_words or settings.WINDOW_MAX_WORDS
        self.min_silence = min_silence or settings.WINDOW_MIN_SILENCE
        self.buf: list[Utterance] = []
        self.words = 0

    def add(self, u: Utterance) -> Window | None:
        if not self.buf:
            self.buf = [u]
            self.words = u.words or len(u.text.split())
            return None
        prev = self.buf[-1]
        gap = u.t_start - prev.t_end
        prior_elapsed = prev.t_end - self.buf[0].t_start
        elapsed = u.t_end - self.buf[0].t_start
        new_words = self.words + (u.words or len(u.text.split()))

        # A long silence belongs between windows. Close the existing window
        # before adding the first utterance after the gap.
        if prior_elapsed >= self.min_s and gap >= self.min_silence:
            w = Window(utterances=self.buf, t_start=self.buf[0].t_start,
                       t_end=self.buf[-1].t_end)
            self.buf = [u]
            self.words = u.words or len(u.text.split())
            return w

        # Do not make an existing window exceed a hard limit just because the
        # next utterance crosses it. Carry that utterance into the next window.
        if elapsed > self.max_s or new_words > self.max_words:
            w = Window(utterances=self.buf, t_start=self.buf[0].t_start,
                       t_end=self.buf[-1].t_end)
            self.buf = [u]
            self.words = u.words or len(u.text.split())
            return w

        self.buf.append(u)
        self.words = new_words
        close = (
            elapsed >= self.max_s
            or new_words >= self.max_words
        )
        if close:
            w = Window(utterances=self.buf, t_start=self.buf[0].t_start, t_end=self.buf[-1].t_end)
            self.buf, self.words = [], 0
            return w
        return None

    def flush(self) -> Window | None:
        if not self.buf:
            return None
        w = Window(utterances=self.buf, t_start=self.buf[0].t_start, t_end=self.buf[-1].t_end)
        self.buf, self.words = [], 0
        return w

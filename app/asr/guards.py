"""ASR hallucination guards (§5.4). Required, not optional."""
from __future__ import annotations

import math

BLOCKLIST = {
    "thanks for watching",
    "thanks for watching!",
    "subscribe for more",
    "like and subscribe",
}

NO_SPEECH_PROB_THRESHOLD = 0.6
MIN_AVG_LOGPROB = -1.0


def compression_ratio(text: str) -> float:
    if not text:
        return 0.0
    # crude: unique-char bigrams / total bigrams; low diversity => high compression
    grams = [text[i : i + 2] for i in range(max(len(text) - 1, 1))]
    if not grams:
        return 0.0
    return len(set(grams)) / len(grams)


def should_drop(text: str, avg_logprob: float | None, no_speech_prob: float | None) -> tuple[bool, str]:
    t = text.strip().lower()
    if not t:
        return True, "empty"
    if t in BLOCKLIST:
        return True, "blocklist"
    if no_speech_prob is not None and no_speech_prob > NO_SPEECH_PROB_THRESHOLD:
        return True, "no_speech"
    if avg_logprob is not None and avg_logprob < MIN_AVG_LOGPROB:
        return True, "low_logprob"
    # repetition loops: e.g. "la la la ..." — low diversity
    if len(t) > 20 and compression_ratio(t) < 0.15:
        return True, "repetition"
    if len(t.split()) > 6 and len(set(t.split())) == 1:
        return True, "repetition"
    return False, ""


def average_logprob(probs: list[float]) -> float | None:
    if not probs:
        return None
    logs = [math.log(max(p, 1e-9)) for p in probs]
    return sum(logs) / len(logs)

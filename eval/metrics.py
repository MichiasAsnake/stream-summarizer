"""Eval metrics (§10): WER tracked, speaker-ID acc/FAR, char attribution, dup rate, latency/cost."""
from __future__ import annotations


def wer(ref: list[str], hyp: list[str]) -> float:
    import difflib
    sm = difflib.SequenceMatcher(None, ref, hyp)
    ops = sum(1 for tag, *_ in sm.get_opcodes() if tag != "equal")
    return ops / max(len(ref), 1)


def accuracy(pred: list, gold: list) -> float:
    if not gold:
        return 0.0
    return sum(1 for p, g in zip(pred, gold) if p == g) / len(gold)


def duplicate_rate(names: list[str]) -> float:
    from rapidfuzz import fuzz
    if len(names) < 2:
        return 0.0
    dups = 0
    pairs = 0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairs += 1
            if fuzz.token_set_ratio(names[i].lower(), names[j].lower()) >= 90:
                dups += 1
    return dups / max(pairs, 1)

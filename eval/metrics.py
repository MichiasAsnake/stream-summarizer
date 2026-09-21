"""Evaluation metrics for transcripts, memory, events, and summaries."""
from __future__ import annotations

import re
from collections.abc import Sequence


def normalize_words(value: str | Sequence[str]) -> list[str]:
    text = value if isinstance(value, str) else " ".join(value)
    return re.findall(r"[\w']+", text.casefold())


def edit_distance(ref: Sequence[str], hyp: Sequence[str]) -> int:
    """Standard Levenshtein distance with O(len(hyp)) memory."""
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref_word != hyp_word),
            ))
        previous = current
    return previous[-1]


def wer(ref: str | Sequence[str], hyp: str | Sequence[str]) -> float:
    """Word error rate: (substitutions + deletions + insertions) / ref words."""
    ref_words = normalize_words(ref)
    hyp_words = normalize_words(hyp)
    return edit_distance(ref_words, hyp_words) / max(len(ref_words), 1)


def accuracy(pred: list, gold: list) -> float:
    if not gold:
        return 0.0
    return sum(1 for p, g in zip(pred, gold, strict=False) if p == g) / len(gold)


def collection_prf(pred: Sequence[str], gold: Sequence[str],
                   threshold: float = 85.0) -> dict[str, float]:
    """Greedy fuzzy matching for unordered event/entity/thread fact sets."""
    from rapidfuzz import fuzz

    remaining = list(pred)
    matched = 0
    for expected in gold:
        if not remaining:
            break
        scores = [fuzz.token_set_ratio(expected.casefold(), item.casefold())
                  for item in remaining]
        best_index = max(range(len(scores)), key=scores.__getitem__)
        if scores[best_index] >= threshold:
            matched += 1
            remaining.pop(best_index)
    precision = matched / len(pred) if pred else (1.0 if not gold else 0.0)
    recall = matched / len(gold) if gold else (1.0 if not pred else 0.0)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "matched": matched}


def required_fact_recall(summary: str, facts: Sequence[str],
                         threshold: float = 80.0) -> float:
    """Fraction of required facts recoverable from a generated summary."""
    from rapidfuzz import fuzz

    if not facts:
        return 1.0
    return round(sum(
        fuzz.partial_token_set_ratio(fact.casefold(), summary.casefold()) >= threshold
        for fact in facts
    ) / len(facts), 4)


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


def evaluate_snapshot(snapshot: dict, gold: dict) -> dict:
    """Score one serialized pipeline snapshot against a compact gold file."""
    metrics: dict[str, object] = {}
    if "transcript" in gold:
        metrics["wer"] = round(wer(gold["transcript"], snapshot.get("transcript", "")), 4)
    for key, threshold in (("events", 80.0), ("entities", 95.0), ("threads", 85.0)):
        if key in gold:
            metrics[key] = collection_prf(snapshot.get(key, []), gold[key], threshold)
    if "summary_facts" in gold:
        generated = "\n".join(snapshot.get("window_summaries", [])
                               + snapshot.get("rolling_summaries", [])
                               + [snapshot.get("recap", "")])
        metrics["summary_fact_recall"] = required_fact_recall(
            generated, gold["summary_facts"])
    metrics["entity_duplicate_rate"] = round(
        duplicate_rate(snapshot.get("entities", [])), 4)
    return metrics

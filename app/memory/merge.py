"""Entity merge suggestions (§5.9): Jev Noul adjudication over candidate pairs.

Candidate generation is pure code (shared aliases, fuzzy canonical match);
the same-or-not question — absolute, textual, constrained — goes to Jev in a
single fan-out call. Suggestions are review-only; applying uses the existing
POST /entities/merge endpoint.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.classify.classifier import JevClassifier, get_classifier
from app.db import models as m

MERGE_THRESHOLD = 0.75  # noul >= this -> "merge"
REVIEW_THRESHOLD = 0.5  # noul >= this -> "review", else dropped


def candidate_pairs(db: SASession, channel_id: int, limit: int = 25
                    ) -> list[tuple[m.Entity, m.Entity]]:
    """Pairs of live entities that might be the same: shared alias text
    (either direction) or fuzzy canonical match. Ordered by combined
    mentions so established records come first."""
    ents = db.execute(select(m.Entity).where(
        m.Entity.channel_id == channel_id,
        m.Entity.status != "merged")).scalars().all()
    aliases: dict[int, list[str]] = {}
    for e in ents:
        aliases[e.id] = [a.alias for a in db.execute(
            select(m.EntityAlias).where(m.EntityAlias.entity_id == e.id)).scalars().all()]
    try:
        from rapidfuzz import fuzz as _fuzz
    except ImportError:
        _fuzz = None
    pairs: list[tuple[m.Entity, m.Entity]] = []
    for i, a in enumerate(ents):
        names_a = {a.canonical_name.lower()} | {x.lower() for x in aliases[a.id]}
        for b in ents[i + 1:]:
            names_b = {b.canonical_name.lower()} | {x.lower() for x in aliases[b.id]}
            if names_a & names_b or _fuzz is not None and _fuzz.token_set_ratio(
                    a.canonical_name.lower(), b.canonical_name.lower()) >= 85:
                pairs.append((a, b))
            if len(pairs) >= limit:
                return pairs
    pairs.sort(key=lambda p: -(p[0].mention_count + p[1].mention_count))
    return pairs[:limit]


def _pair_question(a: m.Entity, b: m.Entity,
                   aliases: dict[int, list[str]]) -> dict:
    return {
        "type": "noul",
        "instructions": (f"Entity {a.id} ('{a.canonical_name}') and entity {b.id} "
                         f"('{b.canonical_name}') are the same person, character, or thing."),
        "criteria": {
            "true": ("The names are variants, nicknames, or aliases of one individual, "
                     "or the descriptions clearly describe the same individual."),
            "false": ("Different individuals who happen to share a first name, "
                      "or the descriptions describe different individuals."),
        },
    }


def suggest_merges(db: SASession, channel_id: int, classifier=None,
                   threshold: float = MERGE_THRESHOLD,
                   limit: int = 25) -> list[dict]:
    """Ask Jev once about all candidate pairs. Returns suggestions with band
    'merge' (>= threshold) or 'review' (>= 0.5). Direction: the lower id
    (original record) absorbs the later duplicate."""
    pairs = candidate_pairs(db, channel_id, limit=limit)
    if not pairs:
        return []
    clf = classifier
    if clf is None:
        clf = get_classifier()
        if not isinstance(clf, JevClassifier):
            raise RuntimeError("Jev not available: set CLASSIFIER=jev + JEV_ENABLED=1")
    aliases: dict[int, list[str]] = {}
    for e in {e for p in pairs for e in p}:
        aliases[e.id] = [a.alias for a in db.execute(
            select(m.EntityAlias).where(m.EntityAlias.entity_id == e.id)).scalars().all()]

    def _desc(e: m.Entity) -> str:
        aka = ", ".join(sorted(set(aliases[e.id]))) or "—"
        return (f"E{e.id} '{e.canonical_name}' [{e.type}, {e.status}, "
                f"{e.mention_count} mentions]: {e.description or '—'} | also known as: {aka}")

    state = "\n".join(_desc(e) for e in sorted(
        {e for p in pairs for e in p}, key=lambda e: e.id))
    questions = {f"pair_{a.id}_{b.id}": _pair_question(a, b, aliases) for a, b in pairs}
    data = clf.ask(state, questions)
    answers = data.get("answers", {})
    out = []
    for a, b in pairs:
        try:
            noul = float(answers.get(f"pair_{a.id}_{b.id}", {}).get("noul", 0))
        except (TypeError, ValueError):
            continue
        if noul < REVIEW_THRESHOLD:
            continue
        into, frm = (a, b) if a.id < b.id else (b, a)
        band = "merge" if noul >= threshold else "review"
        try:
            from app.memory.streamer import is_streamer_entity as _is_st
            if _is_st(db, channel_id, a.id) or _is_st(db, channel_id, b.id):
                # Never auto-merge anything into (or out of) the streamer
                # entity: alias collisions go to human review.
                band = "review"
        except Exception:
            pass
        out.append({"from_id": frm.id, "into_id": into.id,
                    "from_name": frm.canonical_name, "into_name": into.canonical_name,
                    "noul": round(noul, 3),
                    "band": band,
                    "model": data.get("model", "")})
    return out

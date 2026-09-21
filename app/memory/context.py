"""Context assembler (§5.7): ≤~3000-token pack: meta + cast(12) + threads(6) + history + speakers."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def build_context(db: SASession, channel_id: int, session_id: int,
                  speaker_labels: list[str],
                  rolling: str = "", recent_windows: list[str] | None = None) -> str:
    recent_windows = recent_windows or []
    parts: list[str] = []
    # Stream meta
    sess = db.get(m.Session, session_id)
    meta = f"title={getattr(sess, 'title', '')} category={getattr(sess, 'category', '')}"
    parts.append(f"[meta] {meta}")
    # POV anchor (2.5a): streamer identity from channel config. First position
    # keeps it out of the truncation cut; cast suffix aids resolution.
    try:
        from app.memory.streamer import get_streamer as _get_streamer
        _st = _get_streamer(db, channel_id, session_id)
        _chars = ", ".join(c.get("name", "") for c in (_st.get("characters") or []))
        parts.append(f"[streamer] {_st['name']} (entity E{_st.get('entity_id')})"
                     f" aka {', '.join(_st.get('aliases') or [])}"
                     f" | character: {_chars} | POV: {_st.get('pov_mode')}"
                     " — use as the viewing anchor; state their role only when supported")
    except Exception:
        pass
    # Cast: top 12 by recency x mentions (approx: mention_count desc, last_seen desc)
    cast = db.execute(select(m.Entity).where(m.Entity.channel_id == channel_id)
                      .order_by(m.Entity.mention_count.desc(),
                                m.Entity.last_seen_at.desc()).limit(12)).scalars().all()
    for e in cast:
        aliases = db.execute(select(m.EntityAlias.alias).where(
            m.EntityAlias.entity_id == e.id)).scalars().all()
        aka = ", ".join(a for a in aliases if a.lower() != e.canonical_name.lower()[:50])[:120]
        aka_s = f" aka {aka}" if aka else ""
        line = f"[cast] {e.canonical_name}{aka_s} ({e.type}, {e.status}): {(e.description or '')[:240]}"
        try:
            from app.memory.streamer import is_streamer_entity as _is_st
            if _is_st(db, channel_id, e.id):
                line += " [STREAMER]"
        except Exception:
            pass
        parts.append(line)
    # Open threads top 6
    threads = db.execute(select(m.Thread).where(m.Thread.channel_id == channel_id,
                                                m.Thread.status == "open")
                         .order_by(m.Thread.importance.desc()).limit(6)).scalars().all()
    for t in threads:
        parts.append(f"[thread T{t.id}] {t.title}: {(t.summary or '')[:200]}")
    for w in recent_windows[-3:]:
        parts.append(f"[history] {w[:250]}")
    if rolling:
        parts.append(f"[rolling] {rolling[:250]}")
    if speaker_labels:
        parts.append(f"[speakers] {', '.join(speaker_labels[:12])}")
    # Truncate to ~3000 tokens
    out, budget = [], 3000
    for p in parts:
        t = approx_tokens(p)
        if budget - t < 0:
            break
        out.append(p)
        budget -= t
    return "\n".join(out)

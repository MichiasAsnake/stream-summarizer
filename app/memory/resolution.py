"""Entity resolution (§5.9): exact alias -> fuzzy(>=90) w/ review flag -> provisional create."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SASession

from app.db import models as m


def resolve_entity(db: SASession, channel_id: int, ref: str, etype: str = "other",
                   session_id: int | None = None) -> tuple[m.Entity, bool]:
    """Returns (entity, created). Handles 'E12' ids and 'NEW:<name>' refs."""
    ref = ref.strip()
    if ref.startswith("E") and ref[1:].isdigit():
        ent = db.get(m.Entity, int(ref[1:]))
        if ent is not None:
            return ent, False
    name = ref[4:] if ref.startswith("NEW:") else ref
    # 1. exact alias (case-insensitive)
    q = (select(m.Entity).join(m.EntityAlias, m.EntityAlias.entity_id == m.Entity.id)
         .where(m.Entity.channel_id == channel_id, func.lower(m.EntityAlias.alias) == name.lower()))
    ent = db.execute(q).scalars().first()
    if ent:
        return ent, False
    # also check canonical names
    ent = db.execute(select(m.Entity).where(
        m.Entity.channel_id == channel_id, func.lower(m.Entity.canonical_name) == name.lower())
    ).scalars().first()
    if ent:
        return ent, False
    # 2. fuzzy token-set ratio >= 90. Skipped for very short refs:
    # one- and two-char strings (e.g. the streamer alias "X") over-match
    # everything containing the letter; exact alias/canonical covers them.
    if len(name) >= 3:
        try:
            from rapidfuzz import fuzz
            cands = db.execute(select(m.Entity).where(m.Entity.channel_id == channel_id)).scalars().all()
            best, best_score = None, 0.0
            for c in cands:
                for alias in [c.canonical_name] + [a.alias for a in db.execute(
                        select(m.EntityAlias).where(m.EntityAlias.entity_id == c.id)).scalars().all()]:
                    s = fuzz.token_set_ratio(name.lower(), alias.lower())
                    if s > best_score:
                        best, best_score = c, s
            if best is not None and best_score >= 90:
                best.needs_review = 1
                return best, False
        except ImportError:
            pass
    # 3. provisional create
    from datetime import datetime, timezone
    ent = m.Entity(channel_id=channel_id, type=etype, canonical_name=name, status="provisional",
                   mention_count=0, first_seen_session=session_id,
                   last_seen_at=datetime.now(timezone.utc).isoformat())
    db.add(ent)
    db.flush()
    db.add(m.EntityAlias(entity_id=ent.id, alias=name, source="extraction"))
    return ent, True


def touch_entity(db: SASession, ent: m.Entity, delta: str = "") -> None:
    from datetime import datetime, timezone
    ent.mention_count += 1
    ent.last_seen_at = datetime.now(timezone.utc).isoformat()
    if delta:
        ent.description = ((ent.description or "") + " " + delta).strip()[:2000]
    # promote provisional -> confirmed after >=2 windows (§5.9)
    if ent.status == "provisional" and ent.mention_count >= 2:
        ent.status = "confirmed"

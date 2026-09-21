"""Context assembler (§5.7): ≤~3000-token pack.

Order (earlier blocks survive truncation): meta, streamer, previously-on,
cast, threads, recent window history, rolling summary, speakers.

Selection favours what is relevant *now* over all-time popularity:
- cast: the streamer, entities named in the current window, then most recently seen;
- threads: open threads touched within the last few sessions (older ones are
  dormant and only return when the window mentions them), newest activity first;
- previously-on: final summaries of the channel's most recent finished sessions.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m

CAST_LIMIT = 12
THREAD_LIMIT = 6
PREVIOUS_SESSIONS = 2
THREAD_DORMANT_AFTER_SESSIONS = 3


def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def append_bounded(text: str | None, delta: str, limit: int = 2000, head: int = 400) -> str:
    """Append delta, keeping the original head and the newest tail when over
    limit, so the latest information is never the part that gets dropped."""
    out = ((text or "") + " " + (delta or "")).strip()
    if len(out) <= limit:
        return out
    return out[:head].rstrip() + " … " + out[-(limit - head - 3):].lstrip()


def snippet(text: str | None, n: int) -> str:
    """Head + tail view of long memory text: identity first, latest state last."""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    head = n // 3
    return text[:head].rstrip() + " … " + text[-(n - head - 3):].lstrip()


def _mentioned(names: list[str], window_text: str) -> bool:
    for name in names:
        name = name.strip()
        if len(name) < 2:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", window_text, re.IGNORECASE):
            return True
    return False


def _aliases(db: SASession, entity_id: int) -> list[str]:
    return list(db.execute(select(m.EntityAlias.alias)
                           .where(m.EntityAlias.entity_id == entity_id)).scalars())


def select_cast(db: SASession, channel_id: int, window_text: str = "",
                streamer_entity_id: int | None = None,
                limit: int = CAST_LIMIT) -> list[m.Entity]:
    live = db.execute(select(m.Entity).where(
        m.Entity.channel_id == channel_id, m.Entity.status != "merged")
        .order_by(m.Entity.last_seen_at.desc(), m.Entity.mention_count.desc())).scalars().all()
    streamer = [e for e in live if e.id == streamer_entity_id]
    named: list[m.Entity] = []
    if window_text:
        aliases: dict[int, list[str]] = {}
        for eid, alias in db.execute(select(m.EntityAlias.entity_id, m.EntityAlias.alias)
                                     .join(m.Entity, m.Entity.id == m.EntityAlias.entity_id)
                                     .where(m.Entity.channel_id == channel_id)):
            aliases.setdefault(eid, []).append(alias)
        named = [e for e in live if e.id != streamer_entity_id
                 and _mentioned([e.canonical_name, *aliases.get(e.id, [])], window_text)]
    chosen = {e.id for e in streamer + named}
    recent = [e for e in live if e.id not in chosen]
    return (streamer + named + recent)[:limit]


def dormancy_cutoff(db: SASession, channel_id: int, session_id: int,
                    sessions: int = THREAD_DORMANT_AFTER_SESSIONS) -> str | None:
    """Start time of the Nth most recent earlier session: threads untouched
    since then are dormant. None when the channel has fewer sessions."""
    starts = db.execute(select(m.Session.started_at).where(
        m.Session.channel_id == channel_id, m.Session.id < session_id,
        m.Session.started_at.is_not(None))
        .order_by(m.Session.id.desc()).limit(sessions)).scalars().all()
    return starts[-1] if len(starts) == sessions else None


def select_threads(db: SASession, channel_id: int, session_id: int, window_text: str = "",
                   limit: int = THREAD_LIMIT) -> list[m.Thread]:
    open_threads = db.execute(select(m.Thread).where(
        m.Thread.channel_id == channel_id, m.Thread.status == "open")
        .order_by(m.Thread.last_updated_at.desc(), m.Thread.importance.desc())).scalars().all()
    cutoff = dormancy_cutoff(db, channel_id, session_id)

    def active(t: m.Thread) -> bool:
        return cutoff is None or (t.last_updated_at or "") >= cutoff

    mentioned = [t for t in open_threads if window_text and _mentioned([t.title], window_text)]
    rest = [t for t in open_threads if t not in mentioned and active(t)]
    return (mentioned + rest)[:limit]


def previous_summaries(db: SASession, channel_id: int, session_id: int,
                       limit: int = PREVIOUS_SESSIONS) -> list[m.Session]:
    rows = db.execute(select(m.Session).where(
        m.Session.channel_id == channel_id, m.Session.id < session_id,
        m.Session.final_summary.is_not(None))
        .order_by(m.Session.id.desc()).limit(limit)).scalars().all()
    return list(reversed(rows))  # oldest first reads as a timeline


def build_context(db: SASession, channel_id: int, session_id: int,
                  speaker_labels: list[str],
                  rolling: str | None = None, recent_windows: list[str] | None = None,
                  window_text: str = "") -> str:
    parts: list[str] = []
    sess = db.get(m.Session, session_id)
    parts.append(f"[meta] title={getattr(sess, 'title', '') or ''} "
                 f"category={getattr(sess, 'category', '') or ''}")
    # POV anchor (2.5a): streamer identity from channel config.
    streamer_entity_id = None
    try:
        from app.memory.streamer import get_streamer as _get_streamer
        _st = _get_streamer(db, channel_id, session_id)
        streamer_entity_id = _st.get("entity_id")
        _chars = ", ".join(c.get("name", "") for c in (_st.get("characters") or []))
        parts.append(f"[streamer] {_st['name']} (entity E{streamer_entity_id})"
                     f" aka {', '.join(_st.get('aliases') or [])}"
                     f" | character: {_chars} | POV: {_st.get('pov_mode')}"
                     " — use as the viewing anchor; state their role only when supported")
    except Exception:
        pass
    for prev in previous_summaries(db, channel_id, session_id):
        when = (prev.started_at or "")[:10]
        label = f"{when} {prev.title}" if prev.title else when
        parts.append(f"[previously {label}] {snippet(prev.final_summary, 500)}"
                     " — background from an earlier stream; do not report as new")
    for e in select_cast(db, channel_id, window_text, streamer_entity_id):
        aka = ", ".join(a for a in _aliases(db, e.id)
                        if a.lower() != e.canonical_name.lower())[:120]
        aka_s = f" aka {aka}" if aka else ""
        line = f"[cast] {e.canonical_name}{aka_s} ({e.type}, {e.status}): {snippet(e.description, 240)}"
        if streamer_entity_id is not None and e.id == streamer_entity_id:
            line += " [STREAMER]"
        parts.append(line)
    for t in select_threads(db, channel_id, session_id, window_text):
        parts.append(f"[thread T{t.id}] {t.title}: {snippet(t.summary, 200)}")
    if recent_windows is None:
        recent_windows = list(reversed(db.execute(select(m.Window.window_summary).where(
            m.Window.session_id == session_id, m.Window.window_summary.is_not(None),
            m.Window.window_summary != "")
            .order_by(m.Window.id.desc()).limit(3)).scalars().all()))
    for w in recent_windows[-3:]:
        parts.append(f"[history] {w[:250]}")
    if rolling is None:
        rolling = db.execute(select(m.Summary.text).where(
            m.Summary.session_id == session_id, m.Summary.kind == "rolling")
            .order_by(m.Summary.id.desc()).limit(1)).scalar() or ""
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

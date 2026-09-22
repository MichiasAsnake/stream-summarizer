"""Memory writer (§5.9): one DB transaction per window — attributions, entities, threads, events."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.llm.schemas import Extraction
from app.memory.context import append_bounded
from app.memory.resolution import resolve_entity, touch_entity


def write_extraction(db: SASession, channel_id: int, session_id: int, window_id: int,
                     ext: Extraction) -> None:
    from app.config import settings
    from app.observability import MEMORY_UPDATES

    now = datetime.now(UTC).isoformat()
    # entities first (so refs resolve)
    ref_map: dict[str, int] = {}
    for u in ext.entity_updates:
        if u.confidence < settings.MEMORY_ENTITY_MIN_CONFIDENCE:
            MEMORY_UPDATES.labels(kind="entity", outcome="quarantined").inc()
            # The complete low-confidence proposal remains in the window's
            # extraction_json, but it must not create or mutate memory.
            continue
        ent, _ = resolve_entity(db, channel_id, u.ref, u.type, session_id)
        touch_entity(db, ent, u.description_delta)
        for a in u.aliases_seen:
            if not db.get(m.EntityAlias, {"entity_id": ent.id, "alias": a}):
                db.add(m.EntityAlias(entity_id=ent.id, alias=a, source="extraction"))
        ref_map[u.ref] = ent.id
        if u.ref.startswith("E") is False and u.ref.startswith("NEW:"):
            ref_map[f"E{ent.id}"] = ent.id
        MEMORY_UPDATES.labels(kind="entity", outcome="applied").inc()
    # threads
    thread_map: dict[str, int] = {}
    for t in ext.thread_updates:
        if t.confidence < settings.MEMORY_THREAD_MIN_CONFIDENCE:
            MEMORY_UPDATES.labels(kind="thread", outcome="quarantined").inc()
            continue
        if t.ref.startswith("T") and t.ref[1:].isdigit():
            th = db.get(m.Thread, int(t.ref[1:]))
            if th is None or th.channel_id != channel_id:
                continue
        elif t.ref.startswith("NEW:"):
            # Titles are display text: normalize slugs so a model-emitted
            # NEW:snake_case ref never surfaces with underscores.
            th = m.Thread(channel_id=channel_id, title=t.ref[4:].replace("_", " "),
                          summary="", status="open", importance=3,
                          last_updated_at=now)
            db.add(th)
            db.flush()
        else:
            continue
        if t.status == "resolved" and t.confidence < 0.7:
            pass  # require human click (§5.9); keep open
        else:
            th.status = t.status
        th.summary = append_bounded(th.summary, t.delta)
        th.last_updated_at = now
        thread_map[t.ref] = th.id
        MEMORY_UPDATES.labels(kind="thread", outcome="applied").inc()
    # events
    for e in ext.events:
        if e.confidence < settings.MEMORY_EVENT_MIN_CONFIDENCE:
            MEMORY_UPDATES.labels(kind="event", outcome="quarantined").inc()
            continue
        thread_id = None
        if e.thread_ref:
            if e.thread_ref in thread_map:
                thread_id = thread_map[e.thread_ref]
            elif e.thread_ref.startswith("T") and e.thread_ref[1:].isdigit():
                candidate = db.get(m.Thread, int(e.thread_ref[1:]))
                if candidate is not None and candidate.channel_id == channel_id:
                    thread_id = candidate.id
        if thread_id is not None:
            # A thread is as important as the biggest event it has carried.
            th_row = db.get(m.Thread, thread_id)
            th_row.importance = max(th_row.importance or 0, e.importance or 0)
            th_row.last_updated_at = now
        ev = m.Event(session_id=session_id, window_id=window_id, t_start=e.t_start, t_end=e.t_end,
                     type=e.type, description=e.description, importance=e.importance, thread_id=thread_id,
                     streamer_role=e.streamer_role or "unknown")
        db.add(ev)
        db.flush()
        MEMORY_UPDATES.labels(kind="event", outcome="applied").inc()
        for p in e.participants:
            eid = ref_map.get(p)
            if eid is None and p.startswith("E") and p[1:].isdigit():
                candidate = db.get(m.Entity, int(p[1:]))
                if candidate is not None and candidate.channel_id == channel_id:
                    eid = candidate.id
            if eid:
                db.add(m.EventParticipant(event_id=ev.id, entity_id=eid))
    # attributions
    for a in ext.utterance_attributions:
        reliable = a.confidence >= settings.MEMORY_ATTRIBUTION_MIN_CONFIDENCE
        eid = ref_map.get(a.entity_ref or "")
        if eid is None and (a.entity_ref or "").startswith("E"):
            try:
                eid = int(a.entity_ref[1:])  # type: ignore
            except ValueError:
                eid = None
        if eid is not None:
            candidate = db.get(m.Entity, eid)
            if candidate is None or candidate.channel_id != channel_id:
                eid = None
        if not reliable:
            eid = None
        db.merge(m.Attribution(segment_id=a.segment_id,
                               kind=a.kind if reliable else "unclear", entity_id=eid,
                               confidence=a.confidence, evidence=a.evidence[:60], method="llm"))
        MEMORY_UPDATES.labels(
            kind="attribution", outcome="applied" if reliable else "quarantined").inc()
    db.commit()

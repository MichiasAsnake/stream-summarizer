"""Memory writer (§5.9): one DB transaction per window — attributions, entities, threads, events."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.llm.schemas import Extraction
from app.memory.resolution import resolve_entity, touch_entity


def write_extraction(db: SASession, channel_id: int, session_id: int, window_id: int,
                     ext: Extraction) -> None:
    now = datetime.now(timezone.utc).isoformat()
    # entities first (so refs resolve)
    ref_map: dict[str, int] = {}
    for u in ext.entity_updates:
        ent, _ = resolve_entity(db, channel_id, u.ref, u.type, session_id)
        touch_entity(db, ent, u.description_delta)
        for a in u.aliases_seen:
            if not db.get(m.EntityAlias, {"entity_id": ent.id, "alias": a}):
                db.add(m.EntityAlias(entity_id=ent.id, alias=a, source="extraction"))
        ref_map[u.ref] = ent.id
        if u.ref.startswith("E") is False and u.ref.startswith("NEW:"):
            ref_map[f"E{ent.id}"] = ent.id
    # threads
    thread_map: dict[str, int] = {}
    for t in ext.thread_updates:
        if t.ref.startswith("T") and t.ref[1:].isdigit():
            th = db.get(m.Thread, int(t.ref[1:]))
            if th is None:
                continue
        elif t.ref.startswith("NEW:"):
            th = m.Thread(channel_id=channel_id, title=t.ref[4:], summary=t.delta,
                          status="open", importance=3, last_updated_at=now)
            db.add(th)
            db.flush()
        else:
            continue
        if t.status == "resolved" and t.confidence < 0.7:
            pass  # require human click (§5.9); keep open
        else:
            th.status = t.status
        th.summary = ((th.summary or "") + " " + t.delta).strip()[:2000]
        th.last_updated_at = now
        thread_map[t.ref] = th.id
    # events
    for e in ext.events:
        thread_id = None
        if e.thread_ref:
            if e.thread_ref in thread_map:
                thread_id = thread_map[e.thread_ref]
            elif e.thread_ref.startswith("T") and e.thread_ref[1:].isdigit():
                thread_id = int(e.thread_ref[1:])
        ev = m.Event(session_id=session_id, window_id=window_id, t_start=e.t_start, t_end=e.t_end,
                     type=e.type, description=e.description, importance=e.importance, thread_id=thread_id,
                     streamer_role=e.streamer_role or "ambient")
        db.add(ev)
        db.flush()
        for p in e.participants:
            eid = ref_map.get(p)
            if eid is None and p.startswith("E") and p[1:].isdigit():
                eid = int(p[1:])
            if eid:
                db.add(m.EventParticipant(event_id=ev.id, entity_id=eid))
    # attributions
    for a in ext.utterance_attributions:
        eid = ref_map.get(a.entity_ref or "")
        if eid is None and (a.entity_ref or "").startswith("E"):
            try:
                eid = int(a.entity_ref[1:])  # type: ignore
            except ValueError:
                eid = None
        db.merge(m.Attribution(segment_id=a.segment_id, kind=a.kind, entity_id=eid,
                               confidence=a.confidence, evidence=a.evidence[:60], method="llm"))
    db.commit()

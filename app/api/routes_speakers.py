"""Speaker routes: enroll, rename/merge/reassign, delete (real deletion per §11)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db

router = APIRouter()


@router.post("/channels/{cid}/speakers/enroll")
def enroll(cid: int, name: str, role: str = "unknown", consent_note: str = "",
           db: SASession = Depends(get_db)):
    from app.config import settings
    from app.consent import check_enroll_allowed, grant_consent
    allowed, reason = check_enroll_allowed(db, name, consent_note,
                                           bool(settings.CONSENT_REQUIRED))
    if not allowed:
        raise HTTPException(400, reason)
    if bool(settings.CONSENT_REQUIRED) and db.get(m.Speaker, 0) is None:
        pass  # consent recorded below per-speaker
    now = datetime.now(timezone.utc).isoformat()
    sp = m.Speaker(channel_id=cid, name=name, role=role, confirmed=1,
                   consent_note=consent_note, created_at=now)
    db.add(sp)
    db.commit()
    db.refresh(sp)
    if consent_note.strip():
        grant_consent(db, cid, sp.id, consent_note)
    return {"id": sp.id}


@router.post("/speakers/{sid}/consent")
def grant(sid: int, note: str, granted_by: str = "owner", db: SASession = Depends(get_db)):
    """D3: record consent for an existing (e.g. unknown-cluster) speaker."""
    from app.consent import grant_consent
    sp = db.get(m.Speaker, sid)
    if not sp:
        raise HTTPException(404)
    c = grant_consent(db, sp.channel_id, sid, note, granted_by)
    sp.consent_note = note
    db.commit()
    return {"consent_id": c.id}


@router.delete("/speakers/{sid}/consent")
def revoke(sid: int, db: SASession = Depends(get_db)):
    """D3: revoke consent (embeddings stay until speaker deleted via DELETE channel route)."""
    from app.consent import revoke_consent
    sp = db.get(m.Speaker, sid)
    if not sp:
        raise HTTPException(404)
    revoke_consent(db, sid)
    return {"ok": True}


@router.patch("/speakers/{sid}")
def patch_speaker(sid: int, name: str | None = None, role: str | None = None,
                  db: SASession = Depends(get_db)):
    sp = db.get(m.Speaker, sid)
    if not sp:
        raise HTTPException(404)
    if name:
        sp.name = name
    if role:
        sp.role = role
    sp.confirmed = 1
    db.commit()
    return {"ok": True}


@router.post("/speakers/merge")
def merge_speakers(into_id: int, from_id: int, db: SASession = Depends(get_db)):
    # Move embeddings then delete source row
    db.execute(m.SpeakerEmbedding.__table__.update().where(
        m.SpeakerEmbedding.speaker_id == from_id).values(speaker_id=into_id))
    db.delete(db.get(m.Speaker, from_id))
    db.commit()
    return {"ok": True}


@router.post("/segments/{seg_id}/reassign-speaker")
def reassign(seg_id: int, speaker_id: int, db: SASession = Depends(get_db)):
    seg = db.get(m.Segment, seg_id)
    seg.speaker_id = speaker_id
    db.commit()
    return {"ok": True}


@router.delete("/channels/{cid}/speakers/{sid}")
def delete_speaker(cid: int, sid: int, db: SASession = Depends(get_db)):
    """Real deletion: embeddings + derived data (§11 biometric-data rule)."""
    sp = db.get(m.Speaker, sid)
    if not sp or sp.channel_id != cid:
        raise HTTPException(404)
    db.execute(m.SpeakerEmbedding.__table__.delete().where(m.SpeakerEmbedding.speaker_id == sid))
    db.execute(m.Segment.__table__.update().where(m.Segment.speaker_id == sid).values(speaker_id=None))
    db.delete(sp)
    db.commit()
    return {"ok": True}

"""Speaker routes: enroll, rename/merge/reassign, delete (real deletion per §11)."""
from __future__ import annotations

import io
import wave
from datetime import UTC, datetime

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.db.session import get_db

router = APIRouter()


@router.post("/channels/{cid}/speakers/enroll")
def enroll(cid: int, name: str, role: str = "unknown", consent_note: str = "",
           db: SASession = Depends(get_db)):
    from app.config import settings
    from app.consent import check_enroll_allowed, grant_consent
    if db.get(m.Channel, cid) is None:
        raise HTTPException(404, "channel not found")
    allowed, reason = check_enroll_allowed(db, name, consent_note,
                                           bool(settings.CONSENT_REQUIRED))
    if not allowed:
        raise HTTPException(400, reason)
    now = datetime.now(UTC).isoformat()
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
    """Revoke consent and immediately remove persisted voiceprints."""
    from app.consent import revoke_consent
    sp = db.get(m.Speaker, sid)
    if not sp:
        raise HTTPException(404)
    revoke_consent(db, sid)
    return {"ok": True}


def _decode_enrollment_wav(raw: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise HTTPException(422, "voiceprint must be an uncompressed WAV file") from exc
    if width not in (1, 2, 4) or channels < 1 or sample_rate < 8000:
        raise HTTPException(422, "unsupported WAV format")
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    samples = np.frombuffer(frames, dtype=dtype).astype("float32")
    if width == 1:
        samples = (samples - 128.0) / 128.0
    else:
        samples /= float(2 ** (width * 8 - 1))
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if sample_rate != 16000 and len(samples):
        duration = len(samples) / sample_rate
        old_x = np.linspace(0, duration, len(samples), endpoint=False)
        new_x = np.linspace(0, duration, max(1, int(duration * 16000)), endpoint=False)
        samples = np.interp(new_x, old_x, samples).astype("float32")
    duration = len(samples) / 16000
    if duration < 0.5 or duration > 60:
        raise HTTPException(422, "voiceprint audio must be between 0.5 and 60 seconds")
    return samples.astype("float32")


@router.post("/speakers/{sid}/voiceprint")
async def enroll_voiceprint(sid: int, audio: UploadFile = File(...),
                            db: SASession = Depends(get_db)):
    """Create a voiceprint only for a speaker with active consent."""
    from app.config import settings
    from app.consent import has_consent
    from app.speakers.embedder import get_embedder

    speaker = db.get(m.Speaker, sid)
    if speaker is None:
        raise HTTPException(404, "speaker not found")
    if settings.CONSENT_REQUIRED and not has_consent(db, sid):
        raise HTTPException(403, "active consent is required before voiceprint enrollment")
    raw = await audio.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "voiceprint file exceeds 10 MB")
    pcm = _decode_enrollment_wav(raw)
    embedder = get_embedder(settings.SPK_EMBED_MODEL)
    embedding = np.asarray(embedder.embed(pcm), dtype="float32")
    db.execute(m.SpeakerEmbedding.__table__.delete().where(
        m.SpeakerEmbedding.speaker_id == sid,
        m.SpeakerEmbedding.model == embedder.model_name))
    row = m.SpeakerEmbedding(
        speaker_id=sid, embedding=embedding.tobytes(), dim=int(embedding.shape[0]),
        model=embedder.model_name, seconds=len(pcm) / 16000,
        source="consented-upload", created_at=datetime.now(UTC).isoformat())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"embedding_id": row.id, "model": row.model, "seconds": row.seconds}


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
    into = db.get(m.Speaker, into_id)
    source = db.get(m.Speaker, from_id)
    if into is None or source is None:
        raise HTTPException(404, "speaker not found")
    if into.id == source.id or into.channel_id != source.channel_id:
        raise HTTPException(422, "speakers must be distinct and belong to the same channel")
    db.execute(m.SpeakerEmbedding.__table__.update().where(
        m.SpeakerEmbedding.speaker_id == from_id).values(speaker_id=into_id))
    db.delete(source)
    db.commit()
    return {"ok": True}


@router.post("/segments/{seg_id}/reassign-speaker")
def reassign(seg_id: int, speaker_id: int, db: SASession = Depends(get_db)):
    seg = db.get(m.Segment, seg_id)
    speaker = db.get(m.Speaker, speaker_id)
    if seg is None or speaker is None:
        raise HTTPException(404, "segment or speaker not found")
    session = db.get(m.Session, seg.session_id)
    if session is None or session.channel_id != speaker.channel_id:
        raise HTTPException(422, "segment and speaker belong to different channels")
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
    if db.get(m.Channel, cid) is None:
        raise HTTPException(404, "channel not found")

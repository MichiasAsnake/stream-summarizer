"""Enforce transcript retention and biometric-consent storage rules."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.consent import has_consent
from app.db import models as m


def enforce_retention(db: SASession, now: datetime | None = None) -> dict[str, int]:
    """Delete expired closed sessions and unauthorized speaker embeddings.

    The service stores no raw unknown-speaker audio clips. Unknown clusters
    remain process-local; this routine handles the persisted transcript and
    embedding records that do exist.
    """
    now = now or datetime.now(UTC)
    removed_embeddings = 0
    if settings.CONSENT_REQUIRED:
        speaker_ids = db.execute(select(m.Speaker.id)).scalars().all()
        for speaker_id in speaker_ids:
            if not has_consent(db, speaker_id):
                result = db.execute(delete(m.SpeakerEmbedding).where(
                    m.SpeakerEmbedding.speaker_id == speaker_id))
                removed_embeddings += result.rowcount or 0

    from app.mode import retention_defaults
    mode_limit = retention_defaults(settings.APP_MODE)["transcript_days"]
    retention_days = min(settings.TRANSCRIPT_RETENTION_DAYS, mode_limit)
    cutoff = (now - timedelta(days=max(0, retention_days))).isoformat()
    session_ids = db.execute(select(m.Session.id).where(
        m.Session.status.in_(("ended", "failed", "interrupted")),
        m.Session.started_at.is_not(None),
        m.Session.started_at < cutoff,
    )).scalars().all()
    if not session_ids:
        db.commit()
        return {"sessions": 0, "embeddings": removed_embeddings}

    segment_ids = select(m.Segment.id).where(m.Segment.session_id.in_(session_ids))
    event_ids = select(m.Event.id).where(m.Event.session_id.in_(session_ids))
    beat_ids = select(m.PlotBeat.id).where(m.PlotBeat.session_id.in_(session_ids))

    db.execute(delete(m.EventParticipant).where(m.EventParticipant.event_id.in_(event_ids)))
    db.execute(delete(m.BeatEvent).where(
        (m.BeatEvent.event_id.in_(event_ids)) | (m.BeatEvent.beat_id.in_(beat_ids))))
    db.execute(delete(m.Attribution).where(m.Attribution.segment_id.in_(segment_ids)))
    db.execute(delete(m.SegmentEmbedding).where(m.SegmentEmbedding.segment_id.in_(segment_ids)))
    db.execute(delete(m.SummaryFeedback).where(m.SummaryFeedback.session_id.in_(session_ids)))
    db.execute(delete(m.Event).where(m.Event.session_id.in_(session_ids)))
    db.execute(delete(m.Segment).where(m.Segment.session_id.in_(session_ids)))
    db.execute(delete(m.Window).where(m.Window.session_id.in_(session_ids)))
    db.execute(delete(m.Summary).where(m.Summary.session_id.in_(session_ids)))
    db.execute(delete(m.IngestGap).where(m.IngestGap.session_id.in_(session_ids)))
    db.execute(delete(m.BeatEvent).where(m.BeatEvent.beat_id.in_(beat_ids)))
    db.execute(delete(m.PlotBeat).where(m.PlotBeat.session_id.in_(session_ids)))
    db.execute(delete(m.BeatRun).where(m.BeatRun.session_id.in_(session_ids)))
    db.execute(update(m.Entity).where(m.Entity.first_seen_session.in_(session_ids))
               .values(first_seen_session=None))
    removed_sessions = db.execute(delete(m.Session).where(
        m.Session.id.in_(session_ids))).rowcount or 0
    db.commit()
    return {"sessions": removed_sessions, "embeddings": removed_embeddings}

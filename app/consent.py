"""D3 — Voice-enrollment consent path (§14.3, §11).

Voice prints are biometric data (BIPA/GDPR). Rule: no enrollment without a
consent record. Unknown-speaker clips carry a TTL and are never a searchable
DB of non-consenting people.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def grant_consent(db: SASession, channel_id: int, speaker_id: int, note: str,
                 granted_by: str = "owner") -> m.Consent:
    c = m.Consent(channel_id=channel_id, speaker_id=speaker_id, note=note,
                  granted_by=granted_by, granted_at=utcnow(), revoked_at=None)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def has_consent(db: SASession, speaker_id: int) -> bool:
    row = db.execute(select(m.Consent).where(m.Consent.speaker_id == speaker_id)
                     .order_by(m.Consent.id.desc())).scalars().first()
    return row is not None and row.revoked_at is None


def revoke_consent(db: SASession, speaker_id: int) -> None:
    rows = db.execute(select(m.Consent).where(m.Consent.speaker_id == speaker_id,
                                              m.Consent.revoked_at.is_(None))).scalars().all()
    for r in rows:
        r.revoked_at = utcnow()
    db.commit()


def check_enroll_allowed(db: SASession, speaker_name: str, consent_note: str,
                         consent_required: bool) -> tuple[bool, str]:
    """Gate for POST /speakers/enroll. Returns (allowed, reason)."""
    if not consent_required:
        return True, ""
    if consent_note.strip():
        return True, ""
    return False, (f"consent required to enroll '{speaker_name}': provide consent_note "
                    "(who consented + how) or set CONSENT_REQUIRED=0 for private-dev only")


def clip_expired(created_at_iso: str, ttl_hours: float) -> bool:
    try:
        created = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - created > timedelta(hours=ttl_hours)

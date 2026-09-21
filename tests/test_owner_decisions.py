from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.content_profiles import PROFILES, get_hint, validate
from app.deploy import resolve_asr
from app.mode import retention_defaults
from app.consent import check_enroll_allowed, grant_consent, has_consent, revoke_consent
from app.llm.budget import estimate_cost_usd, should_skip_extraction
from app.db.models import Base


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_profiles_valid():
    for p in PROFILES:
        assert get_hint(validate(p))
    try:
        validate("nope")
        assert False
    except ValueError:
        pass


def test_deploy_asr_defaults():
    assert resolve_asr("mac")["ASR_BACKEND"] == "mlx_whisper"
    assert resolve_asr("local_gpu")["ASR_MODEL"] == "large-v3-turbo"
    assert resolve_asr("hosted")["ASR_BACKEND"] == "hosted"
    assert resolve_asr("mac", explicit_backend="stub")["ASR_BACKEND"] == "stub"


def test_consent_gate():
    db = _db()
    ok, _ = check_enroll_allowed(db, "Streamer", "", consent_required=False)
    assert ok
    ok, reason = check_enroll_allowed(db, "Streamer", "", consent_required=True)
    assert not ok and "consent required" in reason
    ok, _ = check_enroll_allowed(db, "Streamer", "streamer said yes on stream", True)
    assert ok


def test_consent_grant_revoke():
    from app.db import models as m
    from datetime import datetime, timezone
    db = _db()
    now = datetime.now(timezone.utc).isoformat()
    ch = m.Channel(twitch_login="x", created_at=now)
    db.add(ch)
    db.commit()
    sp = m.Speaker(channel_id=ch.id, name="S", created_at=now)
    db.add(sp)
    db.commit()
    grant_consent(db, ch.id, sp.id, "yes on stream")
    assert has_consent(db, sp.id)
    revoke_consent(db, sp.id)
    assert not has_consent(db, sp.id)


def test_budget_caps():
    assert estimate_cost_usd(3000, 600, 0.0005, 0.0015) > 0
    skip, _ = should_skip_extraction({"monthly_exceeded": False, "session_exceeded": True})
    assert skip
    skip, _ = should_skip_extraction({"monthly_exceeded": False, "session_exceeded": False})
    assert not skip


def test_public_retention_stricter():
    assert (retention_defaults("public")["transcript_days"]
            < retention_defaults("private")["transcript_days"])


def test_jev_gated():
    from app.classify.classifier import get_classifier, LlmClassifier
    from app.config import settings
    settings.JEV_ENABLED = False
    assert isinstance(get_classifier("jev"), LlmClassifier)

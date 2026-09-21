from datetime import UTC

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes_sessions import FeedbackIn, get_feedback, post_feedback
from app.db.models import Base


def _db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    from app.db import models as m
    db.add(m.Channel(id=1, twitch_login="test", created_at="2026-01-01T00:00:00+00:00"))
    db.add(m.Session(id=1, channel_id=1, source="replay", status="ended"))
    db.commit()
    return db


def test_feedback_post_and_counts():
    db = _db()
    assert post_feedback(1, FeedbackIn(summary_id=None, kind="recap", vote="no"), db) == {"ok": True}
    post_feedback(1, FeedbackIn(summary_id=None, kind="recap", vote="yes"), db)
    post_feedback(1, FeedbackIn(summary_id=None, kind="rolling", vote="not_sure"), db)
    out = get_feedback(1, db)
    assert out["counts"]["recap"] == {"yes": 1, "no": 1, "not_sure": 0}
    assert out["counts"]["rolling"] == {"yes": 0, "no": 0, "not_sure": 1}
    assert out["total"] == 3


def test_feedback_rejects_bad_vote_and_kind():
    db = _db()
    with pytest.raises(HTTPException):
        post_feedback(1, FeedbackIn(summary_id=None, kind="recap", vote="meh"), db)
    with pytest.raises(HTTPException):
        post_feedback(1, FeedbackIn(summary_id=None, kind="bogus", vote="yes"), db)


def test_negative_recap_vote_invalidates_cache():
    from datetime import datetime

    from app.db import models as m
    db = _db()
    db.add(m.Summary(session_id=1, kind="recap", text="old",
                     created_at=datetime.now(UTC).isoformat()))
    db.commit()
    row = db.query(m.Summary).first()
    row.cache_key = "1:2:3"
    db.commit()
    post_feedback(1, FeedbackIn(summary_id=row.id, kind="recap", vote="no"), db)
    db.refresh(row)
    assert row.cache_key == "stale-feedback"


def test_negative_feedback_simplifies_recap_prompt():
    from datetime import datetime

    from app.db import models as m
    from app.summarize.rolling import build_recap

    seen = []

    class StubLlm:
        def generate_text(self, prompt, **kw):
            seen.append(prompt)
            return "simple recap"

    db = _db()
    for _ in range(2):
        db.add(m.SummaryFeedback(summary_id=None, session_id=1, kind="recap",
                                 vote="no",
                                 created_at=datetime.now(UTC).isoformat()))
    db.commit()
    assert build_recap(db, 1, StubLlm()) == "simple recap"
    assert "short" in seen[0] and "simple" in seen[0]

    db2 = _db()
    assert build_recap(db2, 1, StubLlm()) == "simple recap"
    assert "Recent readers" not in seen[1]

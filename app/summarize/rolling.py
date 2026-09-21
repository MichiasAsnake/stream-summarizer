"""Summarizer (§5.10): rolling (small/fast), recap + final (stronger), from threads+events."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from app.db import models as m
from app.llm import prompts


def update_rolling(llm, prev: str, window_summary: str, events: list[str]) -> str:
    import re
    # Old rolling rows contain a bullets format the prompt no longer asks
    # for — strip markdown fragments so the model doesn't copy the pattern.
    prev_clean = re.split(r"\*+", prev)[0].strip()
    prompt = f"Previous rolling summary:\n{prev_clean}\n\nNew window summary:\n{window_summary}\n\nNew events:\n" + "\n".join(events[:10])
    text = llm.generate_text(prompt, system=prompts.ROLLING_SYSTEM, max_tokens=80)
    return " ".join(text.split()[:30])


def build_recap(db: SASession, session_id: int, llm, max_words: int = 100) -> str:
    sess = db.get(m.Session, session_id)
    thq = select(m.Thread).where(m.Thread.status != "resolved")
    if sess is not None:
        thq = thq.where(m.Thread.channel_id == sess.channel_id)
    threads = db.execute(
        thq.order_by(m.Thread.last_updated_at.desc(), m.Thread.id.desc()).limit(6)
    ).scalars().all()
    # Recency first: take the latest events, then rank those by importance so
    # old pivotal events can't dominate the recap forever.
    recent = db.execute(select(m.Event).where(m.Event.session_id == session_id)
                        .order_by(m.Event.id.desc()).limit(60)).scalars().all()
    events = sorted(recent, key=lambda e: (e.importance or 0, e.id), reverse=True)[:30]
    prev_recaps = db.execute(select(m.Summary).where(
        m.Summary.session_id == session_id, m.Summary.kind == "recap")
        .order_by(m.Summary.id.desc()).limit(1)).scalars().all()
    prev_text = prev_recaps[0].text if prev_recaps else ""
    tpart = "\n".join(f"- {t.title}: {t.summary or ''}" for t in threads)
    epart = "\n".join(f"- [{e.type}] {e.description}" for e in events)
    prompt = (f"Threads:\n{tpart}\n\nEvents (most recent first by relevance):\n{epart}\n\n"
              f"Write a ≤{max_words}-word catch-up recap grouped by storyline, "
              f"emphasizing the most recent developments.")
    if prev_text:
        prompt += (f"\n\nThe user already saw this previous catch-up:\n{prev_text}\n\n"
                   f"Lead with what is NEW since that recap; do not restate it at length.")
    # Confidence feedback loop: recent No / Not Sure votes simplify the style.
    fb = db.execute(select(m.SummaryFeedback.vote).where(
        m.SummaryFeedback.session_id == session_id,
        m.SummaryFeedback.kind == "recap")
        .order_by(m.SummaryFeedback.id.desc()).limit(10)).scalars().all()
    neg = sum(1 for v in fb if v in ("no", "not_sure"))
    if fb and (neg >= 2 or (len(fb) <= 2 and neg == len(fb))):
        prompt += ("\n\nRecent readers found recaps hard to scan: use very short, "
                   "simple sentences and plain words.")
    return llm.generate_text(prompt, system=prompts.RECAP_SYSTEM, max_tokens=200)


def save_summary(db: SASession, session_id: int, kind: str, text: str,
                 t0: float | None = None, t1: float | None = None, model: str = "") -> m.Summary:
    from datetime import datetime, timezone
    s = m.Summary(session_id=session_id, kind=kind, covers_t_start=t0, covers_t_end=t1,
                  text=text, model=model, created_at=datetime.now(timezone.utc).isoformat())
    db.add(s)
    db.commit()
    return s

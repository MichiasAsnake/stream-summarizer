"""D5 — LLM budget enforcement (§8 cost, §5.10 model split).

Session cap (LLM_SESSION_BUDGET_USD) degrades per §8: pause extraction, keep
transcribing, catch up later. Monthly cap (LLM_MONTHLY_BUDGET_USD) is a hard
stop across sessions. Costs are estimated per 1K tokens when the provider
doesn't return exact billing.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SASession

from app.db import models as m


def estimate_cost_usd(tokens_in: int, tokens_out: int,
                      per_1k_in: float, per_1k_out: float) -> float:
    return tokens_in / 1000 * per_1k_in + tokens_out / 1000 * per_1k_out


def session_spend(db: SASession, session_id: int) -> float:
    windows = db.execute(select(func.coalesce(func.sum(m.Window.cost_usd), 0.0))
                         .where(m.Window.session_id == session_id)).scalar()
    summaries = db.execute(select(func.coalesce(func.sum(m.Summary.cost_usd), 0.0))
                           .where(m.Summary.session_id == session_id)).scalar()
    return float(windows or 0.0) + float(summaries or 0.0)


def monthly_spend(db: SASession, month_prefix: str) -> float:
    """month_prefix like '2026-09' matched against windows.created_at."""
    windows = db.execute(select(func.coalesce(func.sum(m.Window.cost_usd), 0.0))
                         .where(m.Window.created_at.like(f"{month_prefix}%"))).scalar()
    summaries = db.execute(select(func.coalesce(func.sum(m.Summary.cost_usd), 0.0))
                           .where(m.Summary.created_at.like(f"{month_prefix}%"))).scalar()
    return float(windows or 0.0) + float(summaries or 0.0)


def budget_status(db: SASession, session_id: int, month_prefix: str,
                  session_cap: float, monthly_cap: float) -> dict:
    sess = session_spend(db, session_id)
    month = monthly_spend(db, month_prefix)
    return {
        "session_spend": sess,
        "session_cap": session_cap,
        "session_exceeded": sess >= session_cap,
        "month_spend": month,
        "monthly_cap": monthly_cap,
        "monthly_exceeded": monthly_cap > 0 and month >= monthly_cap,
    }


def should_skip_extraction(status: dict) -> tuple[bool, str]:
    if status["monthly_exceeded"]:
        return True, "monthly budget exceeded — extraction paused (transcription continues)"
    if status["session_exceeded"]:
        return True, "session budget exceeded — extraction paused (transcription continues)"
    return False, ""

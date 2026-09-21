"""Low-cardinality metrics, logging, and persistent pipeline error state."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from prometheus_client import Counter
from sqlalchemy.orm import Session as SASession

from app.config import settings
from app.db import models as m

T = TypeVar("T")

LLM_REQUESTS = Counter(
    "llm_requests_total", "LLM request attempts", ("stage", "outcome"))
LLM_ERRORS = Counter(
    "llm_errors_total", "LLM request errors", ("stage", "error_type"))
PIPELINE_ERRORS = Counter(
    "pipeline_stage_errors_total", "Pipeline stage errors", ("stage", "error_type"))
MEMORY_UPDATES = Counter(
    "memory_updates_total", "Persistent memory update decisions", ("kind", "outcome"))

log = logging.getLogger("stream_summarizer")


def _error_name(exc: BaseException) -> str:
    return type(exc).__name__[:80]


def report_pipeline_error(stage: str, exc: BaseException) -> None:
    """Emit an operational error without using exception text as a metric label."""
    PIPELINE_ERRORS.labels(stage=stage, error_type=_error_name(exc)).inc()
    log.exception("pipeline stage %s failed", stage, exc_info=exc)


def set_session_error(db: SASession, session_id: int, stage: str,
                      exc: BaseException) -> None:
    session = db.get(m.Session, session_id)
    if session is None:
        return
    session.last_error = f"{stage}: {_error_name(exc)}"
    session.last_error_at = datetime.now(UTC).isoformat()


def clear_session_error(db: SASession, session_id: int, stage: str) -> None:
    session = db.get(m.Session, session_id)
    if session is not None and (session.last_error or "").startswith(f"{stage}:"):
        session.last_error = None
        session.last_error_at = None


def call_llm(stage: str, operation: Callable[[], T]) -> T:
    """Run one provider operation with bounded retries and observable attempts.

    Provider clients still enforce the actual network timeout. This helper
    provides a consistent retry ceiling and backoff across providers.
    """
    retries = max(0, int(settings.LLM_MAX_RETRIES))
    for attempt in range(retries + 1):
        try:
            result = operation()
            LLM_REQUESTS.labels(stage=stage, outcome="success").inc()
            return result
        except Exception as exc:
            LLM_REQUESTS.labels(stage=stage, outcome="error").inc()
            LLM_ERRORS.labels(stage=stage, error_type=_error_name(exc)).inc()
            if attempt >= retries:
                log.exception("LLM stage %s failed after %s attempt(s)",
                              stage, attempt + 1, exc_info=exc)
                raise
            delay = max(0.0, float(settings.LLM_RETRY_BACKOFF_SECONDS)) * (2 ** attempt)
            log.warning("LLM stage %s attempt %s failed (%s); retrying in %.2fs",
                        stage, attempt + 1, _error_name(exc), delay)
            if delay:
                time.sleep(delay)
    raise RuntimeError("unreachable LLM retry state")  # pragma: no cover

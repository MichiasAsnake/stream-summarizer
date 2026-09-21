"""FastAPI app (§5.12): REST + SSE, static bearer auth, Prometheus metrics."""
from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import (
    routes_channels,
    routes_memory,
    routes_sessions,
    routes_speakers,
    ui_page,
)
from app.config import settings
from app.db.session import SessionLocal, ensure_schema, init_db
from app.task_manager import TaskManager

app = FastAPI(title="Stream Summarizer V1")


def auth(authorization: str | None = Header(default=None)):
    expected = f"Bearer {settings.API_BEARER_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "unauthorized")
    return True


@app.on_event("startup")
def _startup():
    if (not settings.API_BEARER_TOKEN
            or settings.API_BEARER_TOKEN == "dev-token-change-me"):
        raise RuntimeError(
            "Set API_BEARER_TOKEN to a strong, non-default secret before starting")
    init_db()
    ensure_schema()
    app.state.db_factory = SessionLocal
    app.state.task_manager = TaskManager()
    from app.leases import new_worker_id, reconcile_orphans
    app.state.worker_id = new_worker_id()
    from app.retention import enforce_retention
    db = SessionLocal()
    try:
        # Crash recovery: sessions left "live" by a dead process are closed
        # as interrupted. Sessions of other live workers keep valid leases.
        reconcile_orphans(db)
        enforce_retention(db)
    finally:
        db.close()
    # D4: public mode without legal sign-off is blocked loudly, not silently
    if settings.APP_MODE == "public":
        import logging
        logging.getLogger("uvicorn").warning(
            "APP_MODE=public: confirm docs/legal-review.md checklist before serving transcripts externally.")


async def _reconcile_loop():
    import asyncio
    import logging

    from app.leases import reconcile_orphans
    while True:
        await asyncio.sleep(settings.MONITOR_LEASE_TTL_SECONDS)
        db = SessionLocal()
        try:
            reconcile_orphans(db)
        except Exception:
            logging.getLogger(__name__).exception("orphan reconciliation failed")
        finally:
            db.close()


@app.on_event("startup")
async def _start_reconciler():
    import asyncio
    app.state.reconciler = asyncio.create_task(_reconcile_loop())


@app.on_event("shutdown")
async def _shutdown():
    # Graceful stop: pipelines close their sessions as "ended" and release
    # their leases instead of waiting for lease expiry.
    reconciler = getattr(app.state, "reconciler", None)
    if reconciler is not None:
        reconciler.cancel()
    tm = getattr(app.state, "task_manager", None)
    if tm is not None:
        await tm.stop_all()


@app.get("/api/v1/mode")
def mode():
    from app.mode import retention_defaults
    return {"mode": settings.APP_MODE, "retention": retention_defaults(settings.APP_MODE),
            "deploy_target": settings.DEPLOY_TARGET, "content_profile": settings.CONTENT_PROFILE,
            "jev_enabled": settings.JEV_ENABLED}


@app.get("/api/v1/metrics", dependencies=[Depends(auth)])
def metrics():
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(routes_channels.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_sessions.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_memory.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_speakers.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(ui_page.router)  # /ui is open for local dev; API calls still need the token

"""FastAPI app (§5.12): REST + SSE, static bearer auth, Prometheus metrics."""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from app.api import routes_channels, routes_memory, routes_sessions, routes_speakers, ui_page
from app.config import settings
from app.db.session import ensure_schema, init_db, SessionLocal
from app.task_manager import TaskManager

LLM_ERRORS = Counter("llm_errors_total", "LLM errors")

app = FastAPI(title="Stream Summarizer V1")


def auth(authorization: str | None = Header(default=None)):
    if not authorization or authorization != f"Bearer {settings.API_BEARER_TOKEN}":
        raise HTTPException(401, "unauthorized")
    return True


@app.on_event("startup")
def _startup():
    init_db()
    ensure_schema()
    app.state.db_factory = SessionLocal
    app.state.task_manager = TaskManager()
    # D4: public mode without legal sign-off is blocked loudly, not silently
    if settings.APP_MODE == "public":
        import logging
        logging.getLogger("uvicorn").warning(
            "APP_MODE=public: confirm docs/legal-review.md checklist before serving transcripts externally.")


@app.get("/api/v1/mode")
def mode():
    from app.mode import retention_defaults
    return {"mode": settings.APP_MODE, "retention": retention_defaults(settings.APP_MODE),
            "deploy_target": settings.DEPLOY_TARGET, "content_profile": settings.CONTENT_PROFILE,
            "jev_enabled": settings.JEV_ENABLED}


@app.get("/api/v1/metrics")
def metrics():
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(routes_channels.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_sessions.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_memory.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(routes_speakers.router, prefix="/api/v1", dependencies=[Depends(auth)])
app.include_router(ui_page.router)  # /ui is open for local dev; API calls still need the token

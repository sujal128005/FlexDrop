"""Liveness and readiness probes (unauthenticated, no sensitive data)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from flexdrop import __version__

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", summary="Readiness probe (database + models)")
def ready(request: Request) -> JSONResponse:
    ctx = request.app.state.ctx
    try:
        ctx.db.ping()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "database": "up", "models": ctx.models.describe()},
    )

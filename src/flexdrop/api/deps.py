"""Shared API dependencies and the idempotent-response helper."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from flexdrop.api.security import Principal
from flexdrop.services.context import AppContext
from flexdrop.services.idempotency import run_idempotent


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def idempotent_response(
    request: Request,
    principal: Principal,
    payload: BaseModel,
    status_code: int,
    action: Callable[[], BaseModel],
) -> JSONResponse:
    """Run ``action`` honouring the optional ``Idempotency-Key`` header."""
    ctx: AppContext = request.app.state.ctx
    key = request.headers.get("Idempotency-Key")
    body = (request.url.path + "|" + payload.model_dump_json()).encode()

    def run() -> tuple[int, dict]:
        return status_code, action().model_dump(mode="json")

    status, content, replayed = run_idempotent(ctx.db, principal.name, key, request.method, request.url.path, body, run)
    response = JSONResponse(status_code=status, content=content)
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return response

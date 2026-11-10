"""Uniform JSON error responses: ``{"error": {code, message, request_id, details}}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from flexdrop.errors import FlexDropError, RateLimitedError
from flexdrop.logging_config import request_id_var

log = logging.getLogger(__name__)


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id_var.get(), "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FlexDropError)
    async def _domain(_: Request, exc: FlexDropError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(max(1, round(exc.details.get("retry_after_s", 1))))
        if exc.status_code >= 500:
            log.error("server error: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details), headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body("validation_error", "request validation failed", {"errors": errors})
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content=_body("internal_error", "internal server error"))

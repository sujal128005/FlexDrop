"""HTTP middleware: request IDs, body-size limit, security headers, access logs, Prometheus."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry, Counter, Histogram

from flexdrop.logging_config import request_id_var

log = logging.getLogger("flexdrop.access")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # OpenStreetMap's tile policy requires a Referer; send only the origin to other sites.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
DOCS_CSP = "default-src 'self' 'unsafe-inline' cdn.jsdelivr.net; img-src 'self' data: fastapi.tiangolo.com"


class HttpMetrics:
    def __init__(self, registry: CollectorRegistry):
        self.requests = Counter(
            "flexdrop_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=registry
        )
        self.latency = Histogram(
            "flexdrop_http_request_seconds", "HTTP request latency", ["method", "route"], registry=registry
        )
        self.optimization = Histogram(
            "flexdrop_optimization_seconds",
            "Optimisation run latency",
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
            registry=registry,
        )


def install_middleware(app: FastAPI, max_request_bytes: int, metrics: HttpMetrics) -> None:
    @app.middleware("http")
    async def _middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > max_request_bytes):
                response: Response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {"code": "payload_too_large", "message": "request body too large", "request_id": rid}
                    },
                )
            else:
                response = await call_next(request)
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            elapsed = time.perf_counter() - started
            metrics.requests.labels(request.method, route_path, str(response.status_code)).inc()
            metrics.latency.labels(request.method, route_path).observe(elapsed)
            response.headers["X-Request-ID"] = rid
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            if request.url.path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
                response.headers["Content-Security-Policy"] = DOCS_CSP
            elif request.url.path == "/app" or request.url.path.startswith("/app/"):
                response.headers["Content-Security-Policy"] = APP_CSP
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": route_path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CollectorRegistry

from flexdrop import __version__
from flexdrop.api.errors import install_error_handlers
from flexdrop.api.middleware import HttpMetrics, install_middleware
from flexdrop.api.routers import admin, catalog, dispatch, health, orders, views
from flexdrop.api.security import ApiKeyAuthenticator, RateLimiter
from flexdrop.config import Settings, get_settings
from flexdrop.logging_config import configure_logging
from flexdrop.services.context import AppContext
from flexdrop.services.dispatch import DispatchService
from flexdrop.services.orders import OrderService

DESCRIPTION = """
FlexDrop turns **customer-approved delivery flexibility** into an explicit optimisation
variable. Checkout registers orders and receives flexible-delivery offers; the customer's
explicit choice is recorded; dispatch runs the optimiser and reports execution events, which
trigger continuous re-optimisation. All endpoints require an `X-API-Key`; POST endpoints accept
an optional `Idempotency-Key`.
"""


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    production = settings.env == "production"
    ctx = ctx or AppContext.build(settings, create_schema=settings.auto_create_schema and not production)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        ctx.db.engine.dispose()

    docs = settings.expose_docs and not production
    app = FastAPI(
        title="FlexDrop API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    registry = CollectorRegistry()
    app.state.ctx = ctx
    app.state.authenticator = ApiKeyAuthenticator(settings.parsed_api_keys())
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.order_service = OrderService(ctx)
    app.state.dispatch_service = DispatchService(ctx)
    app.state.prom_registry = registry
    app.state.http_metrics = HttpMetrics(registry)

    install_middleware(app, settings.max_request_bytes, app.state.http_metrics)
    install_error_handlers(app)
    for r in (health.router, catalog.router, orders.router, dispatch.router, views.router, admin.router):
        app.include_router(r)
    install_web_ui(app)
    return app


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def install_web_ui(app: FastAPI) -> None:
    """Serve the browser UI at ``/app``. The UI authenticates with an API key like any client.

    ``/app/session`` hands the UI a key and the demo world **only** when the app was built by
    ``flexdrop studio`` (``app.state.demo``); a normal deployment returns 404 and the UI asks
    the user for a key instead.
    """
    app.state.demo = None

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    @app.get("/app/session", include_in_schema=False)
    def demo_session(request: Request) -> JSONResponse:
        demo = request.app.state.demo
        if demo is None:
            return JSONResponse(
                status_code=404, content={"error": {"code": "not_found", "message": "not in studio mode"}}
            )
        return JSONResponse(content=demo.session_payload())

    if WEB_DIR.is_dir():
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")

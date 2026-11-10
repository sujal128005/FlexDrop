"""Metrics and administration (kill switch)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from flexdrop.api.deps import get_ctx
from flexdrop.api.security import Principal, require
from flexdrop.db.base import utcnow
from flexdrop.schemas import KillSwitchIn, KillSwitchOut, MetricsOut
from flexdrop.services.common import kill_switch_state, set_kill_switch
from flexdrop.services.context import AppContext
from flexdrop.services.metrics import compute_metrics

router = APIRouter(tags=["admin"])


@router.get("/v1/metrics", response_model=MetricsOut, summary="Operational KPIs over a time window")
def metrics(
    window_hours: float = Query(default=24.0, gt=0, le=24 * 31),
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("analyst", "dispatch")),
) -> MetricsOut:
    with ctx.db.session() as s:
        values = compute_metrics(s, ctx.engine_config, window_hours, ctx.settings.kill_switch_default)
    return MetricsOut(window_hours=window_hours, generated_at=utcnow(), values=values)


@router.get("/metrics/prometheus", include_in_schema=False)
def prometheus(request: Request) -> Response:
    ctx: AppContext = request.app.state.ctx
    if not ctx.settings.prometheus_public:
        principal = request.app.state.authenticator.authenticate(request.headers.get("X-API-Key"))
        if not principal.roles & {"analyst", "admin"}:
            raise HTTPException(status_code=403, detail="requires analyst or admin role")
    return Response(generate_latest(request.app.state.prom_registry), media_type=CONTENT_TYPE_LATEST)


def _kill_out(ctx: AppContext) -> KillSwitchOut:
    with ctx.db.session() as s:
        enabled, row = kill_switch_state(s, ctx.settings.kill_switch_default)
        reason = row.value.split("|", 1)[1] if row and "|" in row.value else None
        return KillSwitchOut(
            enabled=enabled,
            updated_by=row.updated_by if row else None,
            updated_at=row.updated_at if row else None,
            reason=reason,
        )


@router.get("/v1/admin/kill-switch", response_model=KillSwitchOut, summary="Kill-switch state")
def get_kill_switch(ctx: AppContext = Depends(get_ctx), _: Principal = Depends(require("admin"))) -> KillSwitchOut:
    return _kill_out(ctx)


@router.post(
    "/v1/admin/kill-switch",
    response_model=KillSwitchOut,
    summary="Enable/disable FlexDrop instantly (standard dispatch continues unaffected)",
)
def post_kill_switch(
    payload: KillSwitchIn, ctx: AppContext = Depends(get_ctx), principal: Principal = Depends(require("admin"))
) -> KillSwitchOut:
    with ctx.db.session() as s:
        set_kill_switch(s, payload.enabled, payload.reason, principal.name)
    return _kill_out(ctx)

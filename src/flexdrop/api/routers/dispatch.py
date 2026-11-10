"""Optimisation runs, plans and plan execution events."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from flexdrop.api.deps import get_ctx, idempotent_response
from flexdrop.api.security import Principal, require
from flexdrop.schemas import OptimizeIn, OptimizeOut, PlanEventIn, PlanEventOut, PlanOut
from flexdrop.services.context import AppContext

router = APIRouter(prefix="/v1", tags=["dispatch"])


@router.post("/optimize", response_model=OptimizeOut, summary="Run the coordination optimiser")
def optimize(
    payload: OptimizeIn, request: Request, principal: Principal = Depends(require("dispatch"))
) -> JSONResponse:
    service = request.app.state.dispatch_service
    metrics = request.app.state.http_metrics

    def action() -> OptimizeOut:
        out: OptimizeOut = service.optimize(payload, principal.name)
        metrics.optimization.observe(out.latency_ms / 1000.0)
        return out

    return idempotent_response(request, principal, payload, 200, action)


@router.get("/plans/{plan_id}", response_model=PlanOut, summary="Get the current version of a plan")
def get_plan(
    plan_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("dispatch")),
) -> PlanOut:
    with ctx.db.session() as s:
        return request.app.state.dispatch_service.get_plan(s, plan_id)


@router.post(
    "/plans/{plan_id}/events",
    response_model=PlanEventOut,
    summary="Report an execution event; triggers continuous re-optimisation when needed",
)
def plan_event(
    plan_id: str, payload: PlanEventIn, request: Request, principal: Principal = Depends(require("dispatch"))
) -> JSONResponse:
    service = request.app.state.dispatch_service
    return idempotent_response(
        request, principal, payload, 200, lambda: service.handle_event(plan_id, payload, principal.name)
    )

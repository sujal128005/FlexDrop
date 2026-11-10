"""Orders, flexible-delivery offers and customer consent."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from flexdrop.api.deps import get_ctx, idempotent_response
from flexdrop.api.security import Principal, require
from flexdrop.db.models import OrderRow
from flexdrop.errors import ConflictError, NotFoundError
from flexdrop.schemas import CancelOrderIn, OrderIn, OrderOut, PlanEventIn, PlanEventType, PreferenceIn, PreferenceOut
from flexdrop.services.context import AppContext

router = APIRouter(prefix="/v1", tags=["orders"])


@router.post(
    "/orders",
    response_model=OrderOut,
    status_code=201,
    summary="Register an order; returns the standard ETA and flexible-delivery offers",
)
def create_order(
    payload: OrderIn, request: Request, principal: Principal = Depends(require("checkout"))
) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    service = request.app.state.order_service

    def action() -> OrderOut:
        with ctx.db.session() as s:
            out, _ = service.create(s, payload, principal.name)
            return out

    return idempotent_response(request, principal, payload, 201, action)


@router.get("/orders/{order_id}", response_model=OrderOut, summary="Get an order")
def get_order(
    order_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("checkout", "dispatch")),
) -> OrderOut:
    with ctx.db.session() as s:
        return request.app.state.order_service.get(s, order_id)


@router.post(
    "/preferences",
    response_model=PreferenceOut,
    summary="Record the customer's explicit delivery choice (standard or a flexible offer)",
)
def record_preference(
    payload: PreferenceIn, request: Request, principal: Principal = Depends(require("checkout"))
) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    service = request.app.state.order_service

    def action() -> PreferenceOut:
        with ctx.db.session() as s:
            return service.record_preference(s, payload, principal.name)

    return idempotent_response(request, principal, payload, 200, action)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderOut,
    summary="Cancel an order before pickup (a planned order is removed from its plan and the plan is re-optimised)",
)
def cancel_order(
    order_id: str,
    payload: CancelOrderIn,
    request: Request,
    principal: Principal = Depends(require("checkout", "dispatch")),
) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx
    orders_service = request.app.state.order_service
    dispatch_service = request.app.state.dispatch_service

    def action() -> OrderOut:
        with ctx.db.session() as s:
            row = s.get(OrderRow, order_id)
            if row is None:
                raise NotFoundError(f"order '{order_id}' not found")
            status, plan_id = row.status, row.plan_id
        if status == "PENDING":
            with ctx.db.session() as s:
                orders_service.cancel_pending(s, order_id, payload.reason, principal.name)
        elif status == "PLANNED" and plan_id is not None:
            dispatch_service.handle_event(
                plan_id,
                PlanEventIn(type=PlanEventType.ORDER_CANCELLED, order_id=order_id, at=payload.at),
                principal.name,
            )
        else:
            raise ConflictError(f"order is {status}; it can only be cancelled before pickup")
        with ctx.db.session() as s:
            return orders_service.get(s, order_id)

    return idempotent_response(request, principal, payload, 200, action)

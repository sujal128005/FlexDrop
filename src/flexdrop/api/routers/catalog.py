"""Stores and delivery partners."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from flexdrop.api.deps import get_ctx, idempotent_response
from flexdrop.api.security import Principal, require
from flexdrop.schemas import PartnerIn, PartnerOut, StoreIn, StoreOut
from flexdrop.services import catalog
from flexdrop.services.context import AppContext

router = APIRouter(prefix="/v1", tags=["catalog"])


@router.post("/stores", response_model=StoreOut, status_code=200, summary="Create or update a store")
def upsert_store(
    payload: StoreIn, request: Request, principal: Principal = Depends(require("dispatch"))
) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx

    def action() -> StoreOut:
        with ctx.db.session() as s:
            out, _ = catalog.upsert_store(s, payload, principal.name)
            return out

    return idempotent_response(request, principal, payload, 200, action)


@router.get("/stores/{store_id}", response_model=StoreOut, summary="Get a store")
def get_store(
    store_id: str, ctx: AppContext = Depends(get_ctx), _: Principal = Depends(require("dispatch", "checkout"))
) -> StoreOut:
    with ctx.db.session() as s:
        return catalog.store_out(catalog.get_store(s, store_id))


@router.post("/partners", response_model=PartnerOut, summary="Create or update partner availability/location")
def upsert_partner(
    payload: PartnerIn, request: Request, principal: Principal = Depends(require("dispatch"))
) -> JSONResponse:
    ctx: AppContext = request.app.state.ctx

    def action() -> PartnerOut:
        with ctx.db.session() as s:
            out, _ = catalog.upsert_partner(s, payload, principal.name)
            return out

    return idempotent_response(request, principal, payload, 200, action)


@router.get("/partners/{partner_id}", response_model=PartnerOut, summary="Get a partner")
def get_partner(
    partner_id: str, ctx: AppContext = Depends(get_ctx), _: Principal = Depends(require("dispatch"))
) -> PartnerOut:
    with ctx.db.session() as s:
        return catalog.partner_out(catalog.get_partner(s, partner_id))

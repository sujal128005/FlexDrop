"""Read models for dashboards: list stores, partners, orders and plans; simulation reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from flexdrop.api.deps import get_ctx
from flexdrop.api.security import Principal, require
from flexdrop.db.base import utcnow
from flexdrop.db.models import OrderRow, PartnerRow, PlanRow, StoreRow
from flexdrop.errors import BusinessRuleError
from flexdrop.schemas import OrderOut, PartnerOut, PlanOut, StoreOut
from flexdrop.services import catalog
from flexdrop.services.common import plan_row_to_out
from flexdrop.services.context import AppContext
from flexdrop.services.orders import OrderService

router = APIRouter(prefix="/v1", tags=["views"])

ORDER_STATUSES = {"PENDING", "PLANNED", "PICKED_UP", "DELIVERED", "CANCELLED"}
PLAN_STATUSES = {"PROPOSED", "ACCEPTED", "IN_PROGRESS", "COMPLETED", "REJECTED", "CANCELLED"}
PARTNER_STATUSES = {"IDLE", "ASSIGNED", "OFFLINE"}
ZONE = Query(default=None, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _statuses(raw: str | None, allowed: set[str]) -> list[str] | None:
    if not raw:
        return None
    values = [v.strip().upper() for v in raw.split(",") if v.strip()]
    bad = sorted(set(values) - allowed)
    if bad:
        raise BusinessRuleError(f"unknown status values {bad}; allowed {sorted(allowed)}")
    return values


@router.get("/stores", response_model=list[StoreOut], summary="List stores")
def list_stores(
    zone: str | None = ZONE,
    limit: int = Query(default=500, ge=1, le=2000),
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("dispatch", "checkout")),
) -> list[StoreOut]:
    q = select(StoreRow).order_by(StoreRow.id).limit(limit)
    if zone:
        q = q.where(StoreRow.zone == zone)
    with ctx.db.session() as s:
        return [catalog.store_out(r) for r in s.scalars(q)]


@router.get("/partners", response_model=list[PartnerOut], summary="List partners")
def list_partners(
    zone: str | None = ZONE,
    status: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=1000, ge=1, le=5000),
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("dispatch")),
) -> list[PartnerOut]:
    q = select(PartnerRow).order_by(PartnerRow.id).limit(limit)
    if zone:
        q = q.where(PartnerRow.zone == zone)
    if wanted := _statuses(status, PARTNER_STATUSES):
        q = q.where(PartnerRow.status.in_(wanted))
    with ctx.db.session() as s:
        return [catalog.partner_out(r) for r in s.scalars(q)]


@router.get("/orders", response_model=list[OrderOut], summary="List recent orders (without offers)")
def list_orders(
    zone: str | None = ZONE,
    status: str | None = Query(default=None, max_length=128),
    since_hours: float = Query(default=24.0, gt=0, le=744),
    limit: int = Query(default=1000, ge=1, le=5000),
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("dispatch")),
) -> list[OrderOut]:
    q = (
        select(OrderRow)
        .where(OrderRow.placed_at >= utcnow() - timedelta(hours=since_hours))
        .order_by(OrderRow.placed_at.desc())
        .limit(limit)
    )
    if zone:
        q = q.where(OrderRow.zone == zone)
    if wanted := _statuses(status, ORDER_STATUSES):
        q = q.where(OrderRow.status.in_(wanted))
    with ctx.db.session() as s:
        return [OrderService.order_out(r, []) for r in s.scalars(q)]


@router.get("/plans", response_model=list[PlanOut], summary="List recent plans (current versions)")
def list_plans(
    zone: str | None = ZONE,
    status: str | None = Query(default=None, max_length=128),
    since_hours: float = Query(default=24.0, gt=0, le=744),
    limit: int = Query(default=500, ge=1, le=2000),
    ctx: AppContext = Depends(get_ctx),
    _: Principal = Depends(require("dispatch")),
) -> list[PlanOut]:
    q = (
        select(PlanRow)
        .options(selectinload(PlanRow.stops))
        .where(PlanRow.created_at >= utcnow() - timedelta(hours=since_hours))
        .order_by(PlanRow.created_at.desc())
        .limit(limit)
    )
    if zone:
        q = q.join(PartnerRow, PartnerRow.id == PlanRow.partner_id).where(PartnerRow.zone == zone)
    if wanted := _statuses(status, PLAN_STATUSES):
        q = q.where(PlanRow.status.in_(wanted))
    with ctx.db.session() as s:
        return [plan_row_to_out(r) for r in s.scalars(q)]


@router.get("/reports/simulation", summary="Simulation comparison produced by `flexdrop pipeline`")
def simulation_reports(
    ctx: AppContext = Depends(get_ctx), _: Principal = Depends(require("analyst", "dispatch"))
) -> dict[str, Any]:
    """Reads ``<reports_dir>/<scenario>_metrics.json`` and ``robustness.json`` (operator-controlled path)."""
    base = Path(ctx.settings.reports_dir)
    scenarios: dict[str, Any] = {}
    latest = 0.0
    for path in sorted(base.glob("*_metrics.json")):
        try:
            scenarios[path.name.removesuffix("_metrics.json")] = json.loads(path.read_text())
            latest = max(latest, path.stat().st_mtime)
        except (OSError, json.JSONDecodeError):
            continue
    robustness: dict[str, Any] | None = None
    rob = base / "robustness.json"
    if rob.exists():
        try:
            robustness = json.loads(rob.read_text())
        except (OSError, json.JSONDecodeError):
            robustness = None
    return {
        "available": bool(scenarios),
        "generated_at": datetime.fromtimestamp(latest, tz=UTC).isoformat() if latest else None,
        "scenarios": scenarios,
        "robustness": robustness,
    }

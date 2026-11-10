"""Shared service helpers: audit trail, kill switch, pseudonymisation and row/domain mapping."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from flexdrop.clock import from_minutes, to_minutes
from flexdrop.db.base import utcnow
from flexdrop.db.models import AuditLogRow, OrderRow, PartnerRow, PlanRow, StoreRow, SystemFlagRow
from flexdrop.domain.models import (
    Category,
    DeliveryMode,
    GeoPoint,
    Handling,
    Order,
    OrderStatus,
    Partner,
    PartnerStatus,
    RoutePlan,
    Store,
    TempClass,
    Vehicle,
)
from flexdrop.logging_config import request_id_var
from flexdrop.schemas import Location, PlanOut, StopOut

KILL_SWITCH_KEY = "kill_switch"


# ------------------------------------------------------------------ audit
def audit(session: Session, actor: str, action: str, entity_type: str, entity_id: str, details: dict[str, Any]) -> None:
    session.add(
        AuditLogRow(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            request_id=request_id_var.get(),
        )
    )


# ------------------------------------------------------------------ kill switch
def kill_switch_state(session: Session, default: bool) -> tuple[bool, SystemFlagRow | None]:
    row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
    if row is None:
        return default, None
    return row.value.startswith("on"), row


def set_kill_switch(session: Session, enabled: bool, reason: str, actor: str) -> SystemFlagRow:
    value = f"{'on' if enabled else 'off'}|{reason}"[:256]
    row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
    if row is None:
        row = SystemFlagRow(key=KILL_SWITCH_KEY, value=value, updated_by=actor)
        session.add(row)
    else:
        row.value = value
        row.updated_by = actor
        row.updated_at = utcnow()
    audit(session, actor, "kill_switch.set", "system", KILL_SWITCH_KEY, {"enabled": enabled, "reason": reason})
    return row


# ------------------------------------------------------------------ privacy
def pseudonymize(customer_id: str, secret: str) -> str:
    """Keyed, irreversible customer reference (HMAC-SHA256). Raw IDs are never stored."""
    return hmac.new(secret.encode(), customer_id.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ mapping
def handling_to_json(h: Handling) -> dict[str, Any]:
    return {
        "temp_classes": sorted(t.value for t in h.temp_classes),
        "volume_l": h.volume_l,
        "weight_kg": h.weight_kg,
        "leak_risk": h.leak_risk,
        "raw_meat": h.raw_meat,
        "sealed": h.sealed,
        "fragile": h.fragile,
    }


def handling_from_json(d: dict[str, Any]) -> Handling:
    return Handling(
        temp_classes=frozenset(TempClass(t) for t in d["temp_classes"]),
        volume_l=float(d["volume_l"]),
        weight_kg=float(d["weight_kg"]),
        leak_risk=bool(d.get("leak_risk", False)),
        raw_meat=bool(d.get("raw_meat", False)),
        sealed=bool(d.get("sealed", True)),
        fragile=bool(d.get("fragile", False)),
    )


def store_to_domain(row: StoreRow) -> Store:
    return Store(row.id, row.name, Category(row.category), GeoPoint(row.lat, row.lng), row.base_prep_min, row.zone)


def order_to_domain(row: OrderRow) -> Order:
    return Order(
        id=row.id,
        store_id=row.store_id,
        category=Category(row.category),
        pickup=GeoPoint(row.pickup_lat, row.pickup_lng),
        drop=GeoPoint(row.drop_lat, row.drop_lng),
        placed_at=to_minutes(row.placed_at),
        predicted_ready_at=to_minutes(row.predicted_ready_at),
        ready_p90_at=to_minutes(row.ready_p90_at),
        promised_at=to_minutes(row.promised_at),
        handling=handling_from_json(row.handling),
        item_count=row.item_count,
        basket_value=row.basket_value,
        mode=DeliveryMode(row.mode),
        max_extra_wait=row.max_extra_wait,
        reward=row.reward,
        status=OrderStatus(row.status),
        zone=row.zone,
    )


def partner_to_domain(row: PartnerRow) -> Partner:
    return Partner(
        id=row.id,
        location=GeoPoint(row.lat, row.lng),
        available_at=to_minutes(row.available_at),
        vehicle=Vehicle(row.vehicle_volume_l, row.vehicle_weight_kg, row.compartments, row.insulated),
        status=PartnerStatus(row.status),
        zone=row.zone,
    )


def cost_breakdown_json(route: RoutePlan) -> dict[str, float]:
    c = route.cost
    return {
        "base": round(c.base, 4),
        "distance": round(c.distance, 4),
        "time": round(c.time, 4),
        "extra_drops": round(c.extra_drops, 4),
        "earnings_top_up": round(c.earnings_top_up, 4),
        "coordination": round(c.coordination, 4),
        "overhead": round(c.overhead, 4),
    }


def plan_row_to_out(row: PlanRow) -> PlanOut:
    stops = [s for s in row.stops if s.version == row.version]
    return PlanOut(
        plan_id=row.id,
        version=row.version,
        status=row.status,
        kind=row.kind,
        partner_id=row.partner_id,
        order_ids=list(row.order_ids),
        stops=[
            StopOut(
                seq=s.seq,
                order_id=s.order_id,
                type=s.stop_type,
                location=Location(lat=s.lat, lng=s.lng),
                eta=s.eta,
                etd=s.etd,
                wait_min=round(s.wait_min, 3),
            )
            for s in sorted(stops, key=lambda s: s.seq)
        ],
        cost_total=round(row.cost_total, 4),
        partner_pay=round(row.partner_pay, 4),
        cost_breakdown=row.cost_breakdown,
        distance_km=round(row.distance_km, 4),
        start_at=row.start_at,
        end_at=row.end_at,
        baseline_cost=round(row.baseline_cost, 4),
        expected_savings=round(row.expected_savings, 4),
        flex_allocation=row.flex_allocation,
        lateness=row.lateness,
        risk_violations=row.risk_violations,
    )


def route_to_out(
    route: RoutePlan, kind: str, baseline: float, plan_id: str | None, version: int, status: str
) -> PlanOut:
    return PlanOut(
        plan_id=plan_id,
        version=version,
        status=status,
        kind=kind,
        partner_id=route.partner_id,
        order_ids=list(route.order_ids),
        stops=[
            StopOut(
                seq=i,
                order_id=s.order_id,
                type=s.type.value,
                location=Location(lat=s.location.lat, lng=s.location.lng),
                eta=from_minutes(s.arrival),
                etd=from_minutes(s.departure),
                wait_min=round(s.wait, 3),
            )
            for i, s in enumerate(route.stops)
        ],
        cost_total=round(route.cost.total, 4),
        partner_pay=round(route.cost.partner_pay, 4),
        cost_breakdown=cost_breakdown_json(route),
        distance_km=round(route.distance_km, 4),
        start_at=from_minutes(route.start_at),
        end_at=from_minutes(route.end_at),
        baseline_cost=round(baseline, 4),
        expected_savings=round(baseline - route.cost.total, 4),
        flex_allocation={k: round(v, 4) for k, v in route.flex_used.items()},
        lateness={k: round(v, 4) for k, v in route.lateness.items()},
        risk_violations=route.risk_violations,
    )


def as_dt(minutes: float) -> datetime:
    return from_minutes(minutes)


def plan_row_to_route(row: PlanRow) -> RoutePlan:
    """Rebuild the engine view of a persisted plan (current version)."""
    from flexdrop.domain.models import CostBreakdown, Stop, StopType

    stops = sorted((s for s in row.stops if s.version == row.version), key=lambda s: s.seq)
    domain_stops = tuple(
        Stop(
            order_id=s.order_id,
            type=StopType(s.stop_type),
            location=GeoPoint(s.lat, s.lng),
            arrival=to_minutes(s.eta),
            departure=to_minutes(s.etd),
            wait=s.wait_min,
        )
        for s in stops
    )
    b = row.cost_breakdown
    return RoutePlan(
        partner_id=row.partner_id,
        order_ids=tuple(row.order_ids),
        stops=domain_stops,
        start_at=to_minutes(row.start_at),
        start_location=domain_stops[0].location if domain_stops else GeoPoint(0.0, 0.0),
        distance_km=row.distance_km,
        cost=CostBreakdown(
            base=b["base"],
            distance=b["distance"],
            time=b["time"],
            extra_drops=b["extra_drops"],
            earnings_top_up=b["earnings_top_up"],
            coordination=b["coordination"],
            overhead=b["overhead"],
        ),
        delivery_at={s.order_id: s.arrival for s in domain_stops if s.type == StopType.DROP},
        flex_used=dict(row.flex_allocation),
        lateness=dict(row.lateness),
        risk_violations=row.risk_violations,
        objective=row.objective,
    )

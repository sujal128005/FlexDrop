"""Stores and delivery partners."""

from __future__ import annotations

from sqlalchemy.orm import Session

from flexdrop.db.base import utcnow
from flexdrop.db.models import PartnerRow, StoreRow
from flexdrop.domain.models import Category, PartnerStatus
from flexdrop.errors import ConflictError, NotFoundError
from flexdrop.schemas import Location, PartnerIn, PartnerOut, StoreIn, StoreOut, VehicleIn
from flexdrop.services.common import audit


def upsert_store(session: Session, payload: StoreIn, actor: str) -> tuple[StoreOut, bool]:
    row = session.get(StoreRow, payload.id)
    created = row is None
    if row is None:
        row = StoreRow(id=payload.id)
        session.add(row)
    row.name = payload.name
    row.category = payload.category.value
    row.lat = payload.location.lat
    row.lng = payload.location.lng
    row.base_prep_min = payload.base_prep_min
    row.zone = payload.zone
    audit(session, actor, "store.upsert", "store", payload.id, {"created": created})
    return store_out(row), created


def store_out(row: StoreRow) -> StoreOut:
    return StoreOut(
        id=row.id,
        name=row.name,
        category=Category(row.category),
        location=Location(lat=row.lat, lng=row.lng),
        base_prep_min=row.base_prep_min,
        zone=row.zone,
    )


def get_store(session: Session, store_id: str) -> StoreRow:
    row = session.get(StoreRow, store_id)
    if row is None:
        raise NotFoundError(f"store '{store_id}' not found")
    return row


def upsert_partner(session: Session, payload: PartnerIn, actor: str) -> tuple[PartnerOut, bool]:
    """Create/update a partner. Location pings for an assigned partner keep it ASSIGNED;
    taking an assigned partner offline must go through the PARTNER_UNAVAILABLE plan event so
    its orders are re-planned (the plan and partner state can never disagree)."""
    if payload.status.value == "ASSIGNED":
        raise ConflictError("status ASSIGNED is set by FlexDrop when a plan is created")
    row = session.get(PartnerRow, payload.id)
    created = row is None
    if row is None:
        row = PartnerRow(id=payload.id, status=payload.status.value)
        session.add(row)
    elif row.status == "ASSIGNED" and payload.status.value == "OFFLINE":
        raise ConflictError(
            f"partner '{payload.id}' is assigned to an active plan; send a PARTNER_UNAVAILABLE plan event",
            {"partner_id": payload.id},
        )
    row.lat = payload.location.lat
    row.lng = payload.location.lng
    if row.status != "ASSIGNED":
        row.status = payload.status.value
        row.available_at = payload.available_at or utcnow()
    row.vehicle_volume_l = payload.vehicle.volume_l
    row.vehicle_weight_kg = payload.vehicle.weight_kg
    row.compartments = payload.vehicle.compartments
    row.insulated = payload.vehicle.insulated
    row.zone = payload.zone
    audit(session, actor, "partner.upsert", "partner", payload.id, {"created": created, "status": row.status})
    return partner_out(row), created


def partner_out(row: PartnerRow) -> PartnerOut:
    return PartnerOut(
        id=row.id,
        location=Location(lat=row.lat, lng=row.lng),
        status=PartnerStatus(row.status),
        available_at=row.available_at,
        vehicle=VehicleIn(
            volume_l=row.vehicle_volume_l,
            weight_kg=row.vehicle_weight_kg,
            compartments=row.compartments,
            insulated=row.insulated,
        ),
        zone=row.zone,
    )


def get_partner(session: Session, partner_id: str) -> PartnerRow:
    row = session.get(PartnerRow, partner_id)
    if row is None:
        raise NotFoundError(f"partner '{partner_id}' not found")
    return row

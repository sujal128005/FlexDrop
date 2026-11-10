"""ORM models. See docs/database.md for the ER diagram and column semantics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flexdrop.db.base import Base, UTCDateTime, utcnow


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class StoreRow(TimestampMixin, Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    base_prep_min: Mapped[float] = mapped_column(Float, nullable=False, default=12.0)
    zone: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    __table_args__ = (CheckConstraint("category IN ('FOOD','GROCERY')", name="category"),)


class PartnerRow(TimestampMixin, Base):
    __tablename__ = "partners"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IDLE", index=True)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    vehicle_volume_l: Mapped[float] = mapped_column(Float, nullable=False, default=45.0)
    vehicle_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=15.0)
    compartments: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    insulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    zone: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (CheckConstraint("status IN ('IDLE','ASSIGNED','OFFLINE')", name="status"),)


class OrderRow(TimestampMixin, Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False, index=True)
    customer_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # pseudonymised
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    pickup_lat: Mapped[float] = mapped_column(Float, nullable=False)
    pickup_lng: Mapped[float] = mapped_column(Float, nullable=False)
    drop_lat: Mapped[float] = mapped_column(Float, nullable=False)
    drop_lng: Mapped[float] = mapped_column(Float, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    predicted_ready_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ready_p90_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    promised_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    basket_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    handling: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="STANDARD")
    max_extra_wait: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reward: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reward_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    zone: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), nullable=True, index=True)
    store_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    picked_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','PLANNED','PICKED_UP','DELIVERED','CANCELLED')", name="status"),
        CheckConstraint("mode IN ('STANDARD','FLEX')", name="mode"),
        CheckConstraint("max_extra_wait >= 0", name="max_extra_wait_nonneg"),
        CheckConstraint("reward >= 0", name="reward_nonneg"),
        Index("ix_orders_status_zone", "status", "zone"),
    )


class FlexOfferRow(Base):
    __tablename__ = "flex_offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    extra_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    acceptance_probability: Mapped[float] = mapped_column(Float, nullable=False)
    coordination_probability: Mapped[float] = mapped_column(Float, nullable=False)
    expected_savings: Mapped[float] = mapped_column(Float, nullable=False)
    expected_net_value: Mapped[float] = mapped_column(Float, nullable=False)
    flex_eta_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class ConsentRow(Base):
    """Explicit customer decision on flexible delivery (the only way a window can change)."""

    __tablename__ = "customer_consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    offer_id: Mapped[str | None] = mapped_column(ForeignKey("flex_offers.id", ondelete="CASCADE"), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    extra_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reward: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="checkout")
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("order_id", name="uq_customer_consents_order_id"),)


class PlanRow(TimestampMixin, Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROPOSED", index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    optimization_run_id: Mapped[str | None] = mapped_column(ForeignKey("optimization_runs.id"), nullable=True)
    order_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    cost_total: Mapped[float] = mapped_column(Float, nullable=False)
    partner_pay: Mapped[float] = mapped_column(Float, nullable=False)
    cost_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    baseline_cost: Mapped[float] = mapped_column(Float, nullable=False)
    expected_savings: Mapped[float] = mapped_column(Float, nullable=False)
    objective: Mapped[float] = mapped_column(Float, nullable=False)
    risk_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flex_allocation: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    lateness: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stops: Mapped[list[PlanStopRow]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="(PlanStopRow.version, PlanStopRow.seq)"
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','ACCEPTED','IN_PROGRESS','COMPLETED','REJECTED','CANCELLED')", name="status"
        ),
    )


class PlanStopRow(Base):
    __tablename__ = "plan_stops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    stop_type: Mapped[str] = mapped_column(String(8), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    eta: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    etd: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    wait_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    plan: Mapped[PlanRow] = relationship(back_populates="stops")
    __table_args__ = (UniqueConstraint("plan_id", "version", "seq", name="uq_plan_stops_plan_version_seq"),)


class PlanEventRow(Base):
    __tablename__ = "plan_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_action: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class OptimizationRunRow(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    solver: Mapped[str] = mapped_column(String(16), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    n_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_partners: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_groups: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_columns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_plans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    objective: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"
    actor: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)


class SystemFlagRow(Base):
    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)

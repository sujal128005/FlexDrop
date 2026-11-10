"""Data retention: purge personal/operational data older than the retention period."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update

from flexdrop.db.base import utcnow
from flexdrop.db.models import (
    AuditLogRow,
    ConsentRow,
    FlexOfferRow,
    IdempotencyRow,
    OptimizationRunRow,
    OrderRow,
    PlanEventRow,
    PlanRow,
    PlanStopRow,
)
from flexdrop.db.session import Database

TERMINAL_ORDER = ("DELIVERED", "CANCELLED")
TERMINAL_PLAN = ("COMPLETED", "REJECTED", "CANCELLED")


def _rows(result: Any) -> int:
    """Affected-row count of a DML statement (``CursorResult.rowcount``)."""
    return int(getattr(result, "rowcount", 0) or 0)


def purge(db: Database, retention_days: int) -> dict[str, int]:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    cutoff = utcnow() - timedelta(days=retention_days)
    counts: dict[str, int] = {}
    with db.session() as s:
        old_plans = list(
            s.scalars(select(PlanRow.id).where(PlanRow.status.in_(TERMINAL_PLAN), PlanRow.end_at < cutoff))
        )
        if old_plans:
            counts["plan_stops"] = _rows(s.execute(delete(PlanStopRow).where(PlanStopRow.plan_id.in_(old_plans))))
            counts["plan_events"] = _rows(s.execute(delete(PlanEventRow).where(PlanEventRow.plan_id.in_(old_plans))))
            s.execute(update(OrderRow).where(OrderRow.plan_id.in_(old_plans)).values(plan_id=None))
            counts["plans"] = _rows(s.execute(delete(PlanRow).where(PlanRow.id.in_(old_plans))))
        referenced = select(PlanStopRow.order_id)
        old_orders = list(
            s.scalars(
                select(OrderRow.id).where(
                    OrderRow.status.in_(TERMINAL_ORDER),
                    OrderRow.placed_at < cutoff,
                    OrderRow.plan_id.is_(None),
                    OrderRow.id.not_in(referenced),
                )
            )
        )
        if old_orders:
            counts["consents"] = _rows(s.execute(delete(ConsentRow).where(ConsentRow.order_id.in_(old_orders))))
            counts["offers"] = _rows(s.execute(delete(FlexOfferRow).where(FlexOfferRow.order_id.in_(old_orders))))
            counts["orders"] = _rows(s.execute(delete(OrderRow).where(OrderRow.id.in_(old_orders))))
        referenced_runs = select(PlanRow.optimization_run_id).where(PlanRow.optimization_run_id.is_not(None))
        counts["optimization_runs"] = _rows(
            s.execute(
                delete(OptimizationRunRow).where(
                    OptimizationRunRow.started_at < cutoff, OptimizationRunRow.id.not_in(referenced_runs)
                )
            )
        )
        counts["audit_logs"] = _rows(s.execute(delete(AuditLogRow).where(AuditLogRow.at < cutoff)))
        counts["idempotency_records"] = _rows(
            s.execute(delete(IdempotencyRow).where(IdempotencyRow.created_at < utcnow() - timedelta(days=2)))
        )
    return counts

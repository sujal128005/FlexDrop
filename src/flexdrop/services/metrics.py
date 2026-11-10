"""Operational metrics computed from the database (pilot KPIs, docs/api.md#metrics)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexdrop.config import EngineConfig
from flexdrop.db.base import utcnow
from flexdrop.db.models import ConsentRow, FlexOfferRow, OptimizationRunRow, OrderRow, PlanRow
from flexdrop.services.common import kill_switch_state, order_to_domain


def compute_metrics(session: Session, cfg: EngineConfig, window_hours: float, kill_default: bool) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=window_hours)
    orders = list(session.scalars(select(OrderRow).where(OrderRow.placed_at >= since)))
    order_ids = {o.id for o in orders}
    status_counts: dict[str, int] = {}
    for o in orders:
        status_counts[o.status] = status_counts.get(o.status, 0) + 1

    deliveries: list[tuple[OrderRow, datetime]] = [
        (o, at) for o in orders if o.status == "DELIVERED" and (at := o.delivered_at) is not None
    ]
    delivered = [o for o, _ in deliveries]
    on_time = [o for o, at in deliveries if at <= o.promised_at + timedelta(minutes=o.max_extra_wait)]
    c2d = [(at - o.placed_at).total_seconds() / 60 for o, at in deliveries]
    quality_risk = 0
    for o, at in deliveries:
        age = (at - o.predicted_ready_at).total_seconds() / 60
        if age > order_to_domain(o).max_age_min(cfg.quality):
            quality_risk += 1

    offered_ids = set(session.scalars(select(FlexOfferRow.order_id).where(FlexOfferRow.order_id.in_(order_ids))))
    consents = list(session.scalars(select(ConsentRow).where(ConsentRow.order_id.in_(order_ids))))
    accepted = [c for c in consents if c.accepted]

    plans = list(session.scalars(select(PlanRow).where(PlanRow.created_at >= since)))
    plan_sizes = {p.id: len(p.order_ids) for p in plans}
    coordinated = [o for o in delivered if o.plan_id and plan_sizes.get(o.plan_id, 1) >= 2]
    completed = [p for p in plans if p.status == "COMPLETED"]
    coordinated_plans = [p for p in completed if len(p.order_ids) >= 2]
    coordinated_orders_in_plans = sum(len(p.order_ids) for p in coordinated_plans)
    rewards_credited = sum(o.reward for o in orders if o.reward_status == "CREDITED")

    runs = list(session.scalars(select(OptimizationRunRow).where(OptimizationRunRow.started_at >= since)))
    latencies = [r.latency_ms for r in runs if r.mode == "FLEXDROP"]
    n_del = len(delivered)
    return {
        "orders_by_status": status_counts,
        "orders_total": len(orders),
        "orders_delivered": n_del,
        "on_time_rate": round(len(on_time) / n_del, 4) if n_del else None,
        "avg_click_to_door_min": round(float(np.mean(c2d)), 3) if c2d else None,
        "p90_click_to_door_min": round(float(np.percentile(c2d, 90)), 3) if c2d else None,
        "quality_risk_deliveries": quality_risk,
        "orders_with_offers": len(offered_ids),
        "offer_acceptance_rate": round(len(accepted) / len(offered_ids), 4) if offered_ids else None,
        "flex_orders": len(accepted),
        "coordinated_share": round(len(coordinated) / n_del, 4) if n_del else None,
        "plans_created": len(plans),
        "plans_completed": len(completed),
        "expected_net_savings_per_coordinated_order": (
            round(sum(p.expected_savings for p in coordinated_plans) / coordinated_orders_in_plans, 4)
            if coordinated_orders_in_plans
            else None
        ),
        "rewards_credited_total": round(rewards_credited, 2),
        "partner_pay_planned_total": round(sum(p.partner_pay for p in completed), 2),
        "optimization_runs": len(runs),
        "optimization_fallbacks": sum(1 for r in runs if r.mode != "FLEXDROP"),
        "optimization_latency_p50_ms": round(float(np.percentile(latencies, 50)), 3) if latencies else None,
        "optimization_latency_p95_ms": round(float(np.percentile(latencies, 95)), 3) if latencies else None,
        "kill_switch_enabled": kill_switch_state(session, kill_default)[0],
        "deployment_level": cfg.deployment_level,
    }

"""Simulation KPIs (docs/simulation.md lists the definitions)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from flexdrop.domain.models import DeliveryMode, OrderStatus

if TYPE_CHECKING:
    from flexdrop.simulation.simulator import Simulator


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


def compute_metrics(sim: Simulator) -> dict[str, Any]:
    orders = list(sim.orders.values())
    deliveries = [
        (s, at) for s in orders if s.order.status == OrderStatus.DELIVERED and (at := s.delivered_at) is not None
    ]
    delivered = [s for s, _ in deliveries]
    cancelled = [s for s in orders if s.order.status == OrderStatus.CANCELLED]
    routes = [r for r in sim.routes.values() if r.completed and r.cost is not None]
    n_del = max(1, len(delivered))

    route_cost = sum(r.cost.total for r in routes if r.cost)
    partner_pay = sum(r.cost.partner_pay for r in routes if r.cost)
    top_up = sum(r.cost.earnings_top_up for r in routes if r.cost)
    rewards = sum(s.order.reward for s in delivered if s.order.mode == DeliveryMode.FLEX)
    distance = sum(r.distance_km for r in routes)

    on_time = [s for s, at in deliveries if at <= s.order.latest_delivery_at + 1e-6]
    std_on_time = [s for s, at in deliveries if at <= s.order.promised_at + 1e-6]
    c2d = [at - s.order.placed_at for s, at in deliveries]
    lateness = [max(0.0, at - s.order.latest_delivery_at) for s, at in deliveries]
    age_violations = len(
        [
            s
            for s, at in deliveries
            if s.picked_at is not None and at - s.true_ready_at > s.order.max_age_min(sim.cfg.quality)
        ]
    )
    flex = [(s, at) for s, at in deliveries if s.order.mode == DeliveryMode.FLEX]
    flex_used = sum(min(max(0.0, at - s.order.promised_at), s.order.max_extra_wait) for s, at in flex)
    flex_approved = sum(s.order.max_extra_wait for s, _ in flex)
    offered = [s for s in orders if s.offers]
    accepted = [s for s in orders if s.accepted is not None]
    coordinated = [s for s in delivered if s.route_size >= 2]

    end = max([sim.scenario.end_at, *(r.end_at for r in routes if r.end_at)])
    logged_h = sum(
        ((sp.offline_at if sp.offline_at is not None else end) - sim.scenario.start_at) / 60.0
        for sp in sim.partners.values()
    )
    engaged_h = sum(sp.engaged_min for sp in sim.partners.values()) / 60.0
    latencies = [e["latency_ms"] for e in sim.epochs]

    return {
        "policy": sim.policy.name,
        "scenario": sim.scenario.spec.name,
        "orders_total": len(orders),
        "orders_delivered": len(delivered),
        "orders_cancelled": len(cancelled),
        "orders_undelivered": len(orders) - len(delivered) - len(cancelled),
        "routes": len(routes),
        "avg_orders_per_route": round(len(delivered) / max(1, len(routes)), 4),
        "coordinated_share": round(len(coordinated) / n_del, 4),
        "fulfilment_cost_per_order": round((route_cost + rewards) / n_del, 4),
        "delivery_cost_per_order": round(route_cost / n_del, 4),
        "rewards_total": round(rewards, 2),
        "rewards_per_order": round(rewards / n_del, 4),
        "partner_pay_total": round(partner_pay, 2),
        "partner_top_up_total": round(top_up, 2),
        "partner_earnings_per_engaged_hour": round(partner_pay / max(1e-9, engaged_h), 4),
        "partner_earnings_per_logged_hour": round(partner_pay / max(1e-9, logged_h), 4),
        "orders_per_partner_logged_hour": round(len(delivered) / max(1e-9, logged_h), 4),
        "orders_per_partner_engaged_hour": round(len(delivered) / max(1e-9, engaged_h), 4),
        "partner_utilisation": round(engaged_h / max(1e-9, logged_h), 4),
        "km_per_order": round(distance / n_del, 4),
        "on_time_rate": round(len(on_time) / n_del, 4),
        "standard_promise_met_rate": round(len(std_on_time) / n_del, 4),
        "avg_click_to_door_min": round(float(np.mean(c2d)) if c2d else 0.0, 4),
        "p90_click_to_door_min": round(_pct(c2d, 90), 4),
        "avg_lateness_min": round(float(np.mean(lateness)) if lateness else 0.0, 4),
        "avg_store_wait_min": round(float(np.mean([s.store_wait for s in delivered])) if delivered else 0.0, 4),
        "food_quality_violations": age_violations,
        "offers_shown": len(offered),
        "flex_accepted": len(accepted),
        "acceptance_rate": round(len(accepted) / max(1, len(offered)), 4),
        "flex_share_of_orders": round(len(accepted) / max(1, len(orders)), 4),
        "flex_utilisation": round(flex_used / flex_approved, 4) if flex_approved else 0.0,
        "optimization_latency_p50_ms": round(_pct(latencies, 50), 3),
        "optimization_latency_p95_ms": round(_pct(latencies, 95), 3),
        "optimization_latency_max_ms": round(max(latencies) if latencies else 0.0, 3),
        **sim.counters,
    }

"""Flexibility Budget Allocation.

Each customer *i* who opts into flexible delivery approves a maximum extra wait ``w_i``.
The optimiser allocates ``s_i`` with the hard constraint ``0 <= s_i <= w_i``; budgets are
individual and never transferable. The allocation is implicit in the chosen delivery time
``a_i``: ``s_i = clip(a_i - promised_i, 0, w_i)`` and ``a_i <= promised_i + w_i``.

This module also computes *dispatch deadlines* that drive the rolling-horizon hold logic:
a flexible order may be held in the pool (waiting for compatible orders) only while a solo
dispatch could still meet its commitment.
"""

from __future__ import annotations

from collections.abc import Iterable

from flexdrop.config import EngineConfig
from flexdrop.domain.models import DeliveryMode, Order, RoutePlan
from flexdrop.engines.travel import TravelOracle

EPS = 1e-6


class FlexibilityViolation(ValueError):
    """Raised when a plan allocates flexibility outside the approved budget."""


class FlexibilityBudget:
    def __init__(self, cfg: EngineConfig, travel: TravelOracle):
        self.cfg = cfg
        self.travel = travel
        self._approach_min = travel.minutes_for_km(cfg.travel.approach_estimate_km)

    def dispatch_deadline(self, order: Order) -> float:
        """Latest time a typical partner can be dispatched and still meet the commitment.

        ``d_i = min(promised_i + w_i, ready_i + age_max_i) - trip_i - service - approach - safety``
        """
        tp = self.cfg.travel
        trip = self.travel.minutes(order.pickup, order.drop)
        latest_drop = min(
            order.latest_delivery_at, order.predicted_ready_at + order.planning_age_limit(self.cfg.quality)
        )
        latest_pickup_departure = latest_drop - trip
        return latest_pickup_departure - tp.pickup_service_min - self._approach_min - self.cfg.optimizer.hold_safety_min

    def is_due(self, order: Order, now: float) -> bool:
        return now + self.cfg.optimizer.epoch_min >= self.dispatch_deadline(order)

    def is_holdable(self, order: Order, now: float) -> bool:
        """A flexible order may wait for coordination while its deadline is not near."""
        return (
            self.cfg.optimizer.hold_enabled
            and order.mode == DeliveryMode.FLEX
            and order.max_extra_wait > 0
            and not self.is_due(order, now)
        )


def validate_allocation(plan: RoutePlan, orders: Iterable[Order]) -> None:
    """Check the flexibility-budget invariant for a plan; raise on violation.

    Applies to strict plans (no lateness). Fallback plans that are late are flagged through
    ``plan.lateness`` and are never presented as using unapproved flexibility.
    """
    by_id = {o.id: o for o in orders}
    for oid, s in plan.flex_used.items():
        o = by_id[oid]
        if s < -EPS or s > o.max_extra_wait + EPS:
            raise FlexibilityViolation(f"order {oid}: allocated {s:.2f} min outside [0, {o.max_extra_wait}]")
        if plan.lateness.get(oid, 0.0) <= EPS and plan.delivery_at[oid] > o.latest_delivery_at + EPS:
            raise FlexibilityViolation(f"order {oid}: delivery after approved window")


def flexibility_utilisation(plans: Iterable[RoutePlan], orders: Iterable[Order]) -> float:
    """Share of approved flexibility minutes actually used: sum(s_i) / sum(w_i)."""
    by_id = {o.id: o for o in orders}
    used = approved = 0.0
    for p in plans:
        for oid, s in p.flex_used.items():
            w = by_id[oid].max_extra_wait
            if w > 0:
                used += s
                approved += w
    return used / approved if approved > 0 else 0.0

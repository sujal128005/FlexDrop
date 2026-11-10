"""Continuous Re-optimisation Engine (CRE).

Reacts to real-world changes on a route that is already in execution:

* preparation delay (new ready time), traffic delay, cancellation -> re-plan the
  *remaining* stops from the partner's projected position;
* partner unavailable -> release every order not yet picked up back to the pool and flag
  on-board orders for rescue.

Guarantees (docs/algorithms.md section 5):

1. A customer never experiences an unapproved extension: every re-plan is validated against
   ``promised_i + w_i``. If the full remaining set cannot be served on time, the engine keeps
   the largest (then cheapest) subset of not-yet-picked orders that is feasible and releases
   the rest to the pool, where the DCO re-dispatches them (fallback columns if needed).
2. Orders on board are never released. If even they cannot be served on time, the route is
   re-sequenced to minimise lateness and the orders are reported as *at risk*.
3. Stability: the current sequence is kept unless a new one improves the objective by at
   least ``insertion_min_gain`` (avoids unnecessary route changes for partners).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations

from flexdrop.config import EngineConfig
from flexdrop.domain.models import GeoPoint, Order, RoutePlan, StopType
from flexdrop.engines.cost import CostModel
from flexdrop.engines.routing import RouteSequencer
from flexdrop.engines.travel import TravelOracle
from flexdrop.ml.models import TravelTimeModel


class ReoptAction(StrEnum):
    UNCHANGED = "UNCHANGED"
    RESEQUENCED = "RESEQUENCED"
    SPLIT = "SPLIT"
    RELEASED_ALL = "RELEASED_ALL"
    AT_RISK = "AT_RISK"
    COMPLETED = "COMPLETED"


@dataclass(slots=True)
class ReoptimizationResult:
    action: ReoptAction
    route: RoutePlan | None
    released_order_ids: list[str] = field(default_factory=list)
    at_risk_order_ids: list[str] = field(default_factory=list)
    reason: str = ""


class ReoptimizationEngine:
    def __init__(self, cfg: EngineConfig, travel_model: TravelTimeModel, cost: CostModel | None = None):
        self.cfg = cfg
        self.travel_model = travel_model
        self.cost = cost or CostModel(cfg.cost)

    def _sequencer(self, now: float, orders: Sequence[Order], start: GeoPoint) -> RouteSequencer:
        oracle = TravelOracle(self.travel_model, self.cfg.travel, now)
        oracle.prime([start, *(o.pickup for o in orders), *(o.drop for o in orders)])
        return RouteSequencer(self.cfg, oracle, self.cost)

    def replan(
        self,
        now: float,
        partner_id: str,
        start_location: GeoPoint,
        start_at: float,
        remaining: Sequence[Order],
        picked: frozenset[str],
        current_sequence: Sequence[tuple[str, StopType]] | None = None,
        reason: str = "",
    ) -> ReoptimizationResult:
        """Re-plan the remaining stops of a route.

        ``remaining`` holds every order still to be delivered on the route (on board or not);
        ``picked`` the subset already on board; ``start_*`` the partner's projected state.
        """
        if not remaining:
            return ReoptimizationResult(ReoptAction.COMPLETED, None, reason=reason)
        seq = self._sequencer(now, remaining, start_location)

        best = seq.best_route(partner_id, start_location, start_at, remaining, picked)
        if best is not None:
            if current_sequence is not None:
                kept = seq.evaluate_sequence(partner_id, start_location, start_at, remaining, current_sequence, picked)
                if kept is not None and kept.objective <= best.objective + self.cfg.optimizer.insertion_min_gain:
                    return ReoptimizationResult(ReoptAction.UNCHANGED, kept, reason=reason)
            return ReoptimizationResult(ReoptAction.RESEQUENCED, best, reason=reason)

        on_board = [o for o in remaining if o.id in picked]
        releasable = [o for o in remaining if o.id not in picked]
        for keep_n in range(len(releasable) - 1, -1, -1):
            candidates: list[tuple[float, RoutePlan, list[Order]]] = []
            for combo in combinations(releasable, keep_n):
                subset = [*on_board, *combo]
                if not subset:
                    continue
                candidate = seq.best_route(partner_id, start_location, start_at, subset, picked)
                if candidate is not None:
                    candidates.append((candidate.objective, candidate, list(combo)))
            if candidates:
                candidates.sort(key=lambda c: (c[0], c[1].order_ids))
                _, split_route, kept_orders = candidates[0]
                kept_ids = {o.id for o in kept_orders}
                released = sorted(o.id for o in releasable if o.id not in kept_ids)
                return ReoptimizationResult(ReoptAction.SPLIT, split_route, released_order_ids=released, reason=reason)

        released = sorted(o.id for o in releasable)
        if not on_board:
            return ReoptimizationResult(ReoptAction.RELEASED_ALL, None, released_order_ids=released, reason=reason)
        rescue = seq.best_route(partner_id, start_location, start_at, on_board, picked, enforce=False)
        at_risk = sorted(oid for oid, late in (rescue.lateness.items() if rescue else []) if late > 1e-6)
        if rescue is None:  # pragma: no cover - relaxed sequencing always yields a route
            at_risk = sorted(o.id for o in on_board)
        return ReoptimizationResult(
            ReoptAction.AT_RISK, rescue, released_order_ids=released, at_risk_order_ids=at_risk, reason=reason
        )

    @staticmethod
    def partner_unavailable(remaining: Sequence[Order], picked: frozenset[str]) -> ReoptimizationResult:
        released = sorted(o.id for o in remaining if o.id not in picked)
        at_risk = sorted(o.id for o in remaining if o.id in picked)
        action = ReoptAction.AT_RISK if at_risk else ReoptAction.RELEASED_ALL
        return ReoptimizationResult(
            action, None, released_order_ids=released, at_risk_order_ids=at_risk, reason="partner_unavailable"
        )

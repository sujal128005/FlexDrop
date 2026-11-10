"""Route sequencing: exact pickup-and-delivery sequencing with time windows.

For a partner and a small group of orders (<= 4 orders, <= 8 stops) we enumerate every
precedence-feasible stop sequence by depth-first branch-and-bound:

* **precedence** - an order's pickup precedes its drop;
* **time windows** - delivery time ``a_i <= promised_i + w_i`` (hard commitment);
* **food quality** - ``a_i - ready_i <= max_age_i``;
* **partner protection** - waiting at a store ``<= max_store_wait`` and route duration
  ``<= max_route_duration``.

Objective of a sequence (minimised)::

    J = C_route(D, T, n) + lambda * sum_i s_i + rho * V_risk   (+ mu * sum_i late_i in fallback)

where ``s_i = clip(a_i - promised_i, 0, w_i)`` is the *allocated flexibility* and
``V_risk`` the number of orders that would miss their commitment under pessimistic (P90
preparation, inflated travel) times. Because the route cost is monotone non-decreasing
along a partial path it is a valid lower bound, so branch-and-bound returns the exact
optimum. Worst case for 4 orders is 8!/2^4 = 2520 leaves.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from flexdrop.config import EngineConfig
from flexdrop.domain.models import GeoPoint, Order, RoutePlan, Stop, StopType
from flexdrop.engines.cost import CostModel
from flexdrop.engines.travel import TravelOracle

EPS = 1e-6
MAX_ORDERS_PER_ROUTE = 6


class RouteSequencer:
    def __init__(self, cfg: EngineConfig, travel: TravelOracle, cost: CostModel):
        self.cfg = cfg
        self.travel = travel
        self.cost = cost
        self.pessimistic = travel.scaled(cfg.optimizer.risk_travel_factor)
        self._group_cache: dict[tuple[str, ...], tuple[list[list[float]], list[list[float]]]] = {}

    def best_route(
        self,
        partner_id: str,
        start_location: GeoPoint,
        start_at: float,
        orders: Sequence[Order],
        picked: frozenset[str] = frozenset(),
        enforce: bool = True,
    ) -> RoutePlan | None:
        """Return the minimum-objective feasible route or ``None`` if none exists.

        ``picked`` lists orders already on board (only their drop remains). With
        ``enforce=False`` time windows, store-wait and quality limits become soft and
        lateness is penalised instead (used for fallback/rescue routing).
        """
        n = len(orders)
        if n == 0:
            return None
        if n > MAX_ORDERS_PER_ROUTE:
            raise ValueError(f"route sequencing supports at most {MAX_ORDERS_PER_ROUTE} orders")
        if len({o.id for o in orders}) != n:
            raise ValueError("duplicate order in route")

        opt = self.cfg.optimizer
        tp = self.cfg.travel
        c = self.cfg.cost
        # node indices: 0 = start, 1..n = pickups, n+1..2n = drops
        tt, km = self._matrices(start_location, orders)
        ready = [o.predicted_ready_at for o in orders]
        latest = [o.latest_delivery_at for o in orders]
        promised = [o.promised_at for o in orders]
        allowance = [o.max_extra_wait for o in orders]
        max_age = [o.planning_age_limit(self.cfg.quality) for o in orders]
        lam = opt.flex_minute_penalty
        late_pen = opt.late_minute_penalty
        svc_p, svc_d = tp.pickup_service_min, tp.drop_service_min
        max_wait = opt.max_store_wait_min + EPS
        max_dur = opt.max_route_duration_min + EPS
        # inline route cost: max(pay, floor) + fixed  (identical to CostModel.route_cost(...).total)
        pay_fixed = c.base_route_fee + c.extra_drop_fee * (n - 1)
        fixed = c.coordination_overhead_per_extra_order * (n - 1) + c.platform_overhead_per_route
        per_km, per_min, floor_rate = c.per_km, c.per_engaged_minute, c.min_partner_hourly_earnings / 60.0

        to_pick0 = 0
        to_drop0 = 0
        for i, o in enumerate(orders):
            if o.id in picked:
                to_drop0 |= 1 << i
            else:
                to_pick0 |= 1 << i

        best_obj = math.inf
        best_path: list[tuple[int, StopType, float, float, float]] | None = None
        best_dist = 0.0
        path: list[tuple[int, StopType, float, float, float]] = []

        def dfs(node: int, t: float, dist: float, to_pick: int, to_drop: int, penalty: float) -> None:
            nonlocal best_obj, best_path, best_dist
            elapsed = t - start_at
            pay = pay_fixed + per_km * dist + per_min * elapsed
            floor = floor_rate * elapsed
            bound = (pay if pay > floor else floor) + fixed + penalty
            if bound >= best_obj - EPS:
                return
            if to_pick == 0 and to_drop == 0:
                violations = self._risk_violations(start_location, start_at, orders, path, picked)
                total = bound + opt.risk_penalty * violations
                if total < best_obj - EPS:
                    best_obj, best_path, best_dist = total, list(path), dist
                return
            row_t = tt[node]
            row_k = km[node]
            for i in range(n):
                bit = 1 << i
                if to_pick & bit:
                    pn = 1 + i
                    arr = t + row_t[pn]
                    wait = ready[i] - arr
                    if wait < 0.0:
                        wait = 0.0
                    dep = arr + wait + svc_p
                    if enforce:
                        if wait > max_wait or dep - start_at > max_dur:
                            continue
                        direct = dep + tt[pn][1 + n + i]
                        if direct > latest[i] + EPS or direct - ready[i] > max_age[i] + EPS:
                            continue
                    path.append((i, StopType.PICKUP, arr, dep, wait))
                    dfs(pn, dep, dist + row_k[pn], to_pick & ~bit, to_drop | bit, penalty)
                    path.pop()
                elif to_drop & bit:
                    dn = 1 + n + i
                    arr = t + row_t[dn]
                    late = arr - latest[i]
                    if late < 0.0:
                        late = 0.0
                    dep = arr + svc_d
                    if enforce and (late > EPS or arr - ready[i] > max_age[i] + EPS or dep - start_at > max_dur):
                        continue
                    s = min(max(0.0, arr - promised[i]), allowance[i])
                    path.append((i, StopType.DROP, arr, dep, 0.0))
                    dfs(dn, dep, dist + row_k[dn], to_pick, to_drop & ~bit, penalty + lam * s + late_pen * late)
                    path.pop()

        dfs(0, start_at, 0.0, to_pick0, to_drop0, 0.0)
        if best_path is None:
            return None
        return self._build_plan(partner_id, start_location, start_at, orders, best_path, best_dist, best_obj)

    def _matrices(
        self, start_location: GeoPoint, orders: Sequence[Order]
    ) -> tuple[list[list[float]], list[list[float]]]:
        """Travel-time and distance matrices; group rows are cached across partners."""
        key = tuple(o.id for o in orders)
        cached = self._group_cache.get(key)
        group_nodes = [*(o.pickup for o in orders), *(o.drop for o in orders)]
        if cached is None:
            # column 0 (back to the start) is never used: routes are open paths
            tt_rows = [[0.0, *(self.travel.minutes(a, b) for b in group_nodes)] for a in group_nodes]
            km_rows = [[0.0, *(self.travel.km(a, b) for b in group_nodes)] for a in group_nodes]
            cached = (tt_rows, km_rows)
            self._group_cache[key] = cached
        tt0 = [0.0, *(self.travel.minutes(start_location, b) for b in group_nodes)]
        km0 = [0.0, *(self.travel.km(start_location, b) for b in group_nodes)]
        return [tt0, *cached[0]], [km0, *cached[1]]

    def evaluate_sequence(
        self,
        partner_id: str,
        start_location: GeoPoint,
        start_at: float,
        orders: Sequence[Order],
        sequence: Sequence[tuple[str, StopType]],
        picked: frozenset[str] = frozenset(),
        enforce: bool = True,
    ) -> RoutePlan | None:
        """Evaluate a *given* stop sequence; ``None`` if invalid or (when enforced) infeasible."""
        index = {o.id: i for i, o in enumerate(orders)}
        expected = {(o.id, StopType.DROP) for o in orders} | {
            (o.id, StopType.PICKUP) for o in orders if o.id not in picked
        }
        if set(sequence) != expected or len(sequence) != len(expected):
            return None
        opt = self.cfg.optimizer
        tp = self.cfg.travel
        t, loc, dist, flex_sum, late_sum = start_at, start_location, 0.0, 0.0, 0.0
        on_board = set(picked)
        path: list[tuple[int, StopType, float, float, float]] = []
        for oid, stop_type in sequence:
            i = index[oid]
            o = orders[i]
            if stop_type == StopType.PICKUP:
                arr = t + self.travel.minutes(loc, o.pickup)
                wait = max(0.0, o.predicted_ready_at - arr)
                if enforce and wait > opt.max_store_wait_min + EPS:
                    return None
                dep = arr + wait + tp.pickup_service_min
                dist += self.travel.km(loc, o.pickup)
                loc = o.pickup
                on_board.add(oid)
            else:
                if oid not in on_board:
                    return None
                arr = t + self.travel.minutes(loc, o.drop)
                wait = 0.0
                late = max(0.0, arr - o.latest_delivery_at)
                if enforce and (
                    late > EPS or arr - o.predicted_ready_at > o.planning_age_limit(self.cfg.quality) + EPS
                ):
                    return None
                dep = arr + tp.drop_service_min
                dist += self.travel.km(loc, o.drop)
                loc = o.drop
                flex_sum += min(max(0.0, arr - o.promised_at), o.max_extra_wait)
                late_sum += late
            if enforce and dep - start_at > opt.max_route_duration_min + EPS:
                return None
            path.append((i, stop_type, arr, dep, wait))
            t = dep
        objective = (
            self.cost.route_cost(dist, t - start_at, len(orders)).total
            + opt.flex_minute_penalty * flex_sum
            + opt.late_minute_penalty * late_sum
            + opt.risk_penalty * self._risk_violations(start_location, start_at, orders, path, picked)
        )
        return self._build_plan(partner_id, start_location, start_at, orders, path, dist, objective)

    def _risk_violations(
        self,
        start_location: GeoPoint,
        start_at: float,
        orders: Sequence[Order],
        path: Sequence[tuple[int, StopType, float, float, float]],
        picked: frozenset[str],
    ) -> int:
        """Replay a sequence with P90 preparation and inflated travel times."""
        tt = self.pessimistic.minutes
        tp = self.cfg.travel
        t, loc, violations = start_at, start_location, 0
        for i, stop_type, *_ in path:
            o = orders[i]
            if stop_type == StopType.PICKUP:
                arr = t + tt(loc, o.pickup)
                ready = o.ready_p90_at if o.id not in picked else arr
                t = max(arr, ready) + tp.pickup_service_min
                loc = o.pickup
            else:
                arr = t + tt(loc, o.drop)
                too_old = arr - o.predicted_ready_at > o.planning_age_limit(self.cfg.quality) + EPS
                if arr > o.latest_delivery_at + EPS or too_old:
                    violations += 1
                t = arr + tp.drop_service_min
                loc = o.drop
        return violations

    def _build_plan(
        self,
        partner_id: str,
        start_location: GeoPoint,
        start_at: float,
        orders: Sequence[Order],
        path: Sequence[tuple[int, StopType, float, float, float]],
        distance: float,
        objective: float,
    ) -> RoutePlan:
        stops: list[Stop] = []
        delivery_at: dict[str, float] = {}
        flex_used: dict[str, float] = {}
        lateness: dict[str, float] = {}
        for i, stop_type, arr, dep, wait in path:
            o = orders[i]
            loc = o.pickup if stop_type == StopType.PICKUP else o.drop
            stops.append(Stop(o.id, stop_type, loc, arr, dep, wait))
            if stop_type == StopType.DROP:
                delivery_at[o.id] = arr
                flex_used[o.id] = min(max(0.0, arr - o.promised_at), o.max_extra_wait)
                lateness[o.id] = max(0.0, arr - o.latest_delivery_at)
        end = stops[-1].departure
        cost = self.cost.route_cost(distance, end - start_at, len(orders))
        violations = self._risk_violations(
            start_location,
            start_at,
            orders,
            path,
            frozenset(o.id for o in orders if not any(s.order_id == o.id and s.type == StopType.PICKUP for s in stops)),
        )
        return RoutePlan(
            partner_id=partner_id,
            order_ids=tuple(o.id for o in orders),
            stops=tuple(stops),
            start_at=start_at,
            start_location=start_location,
            distance_km=distance,
            cost=cost,
            delivery_at=delivery_at,
            flex_used=flex_used,
            lateness=lateness,
            risk_violations=violations,
            objective=objective,
        )

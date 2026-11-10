"""Dynamic Coordination Optimizer (DCO).

Rolling-horizon, column-based optimisation executed once per decision epoch:

1. **Classify** pending orders: *due* (dispatch deadline within the epoch), *holdable*
   (flexible, not due - may wait in the pool for compatible orders) or neither.
2. **Generate candidate groups** with OCI: singletons, pairs from the top-k compatible
   neighbours of each order, and triples/quads that extend them (all pairs compatible).
3. **Price columns**: for each group and each of the nearest idle partners, solve the exact
   route-sequencing problem (:mod:`flexdrop.engines.routing`). Column value::

       v_c = B * |new orders| - J_c

   where ``B`` is the assignment bonus (serving now beats leaving an order unassigned) and
   ``J_c`` the sequencing objective (cost + lambda * flex + risk). Holdable orders may only
   appear in groups of size >= 2 (they wait instead of going solo).
   *Insertion columns* add a pending order to a not-yet-started route (Level 3).
   *Fallback columns* relax time windows for due orders with no feasible column, so no
   order is stranded (lateness is penalised and reported).
4. **Select columns** by weighted set packing (CP-SAT)::

       max  sum_c v_c x_c
       s.t. sum_{c ∋ i} x_c <= 1    for every order i
            sum_{c ∋ k} x_c <= 1    for every partner k / active route r
            x_c ∈ {0, 1}

   A greedy solution is passed as a hint and used as fallback if CP-SAT fails.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from flexdrop.config import EngineConfig
from flexdrop.domain.models import (
    ActiveRoute,
    ColumnKind,
    DispatchDecision,
    OptimizationStats,
    Order,
    OrderStatus,
    Partner,
    PartnerStatus,
    PlannedRoute,
    RoutePlan,
    Vehicle,
)
from flexdrop.engines.compatibility import CompatibilityEngine, handling_violations
from flexdrop.engines.cost import CostModel
from flexdrop.engines.flexibility import FlexibilityBudget
from flexdrop.engines.routing import RouteSequencer
from flexdrop.engines.travel import TravelOracle
from flexdrop.geo import haversine_km
from flexdrop.ml.models import TravelTimeModel

log = logging.getLogger(__name__)


@dataclass(slots=True, eq=False)
class Column:
    kind: ColumnKind
    route: RoutePlan
    value: float
    resources: tuple[str, ...]
    baseline_cost: float
    new_order_ids: tuple[str, ...]
    replaces_route_id: str | None = None
    priority: float = 0.0  # FIFO ordering key (earliest placement among new orders)


@dataclass(slots=True)
class _Context:
    now: float
    oracle: TravelOracle
    compat: CompatibilityEngine
    sequencer: RouteSequencer
    budget: FlexibilityBudget
    baseline: dict[str, float] = field(default_factory=dict)


class DynamicCoordinationOptimizer:
    def __init__(self, cfg: EngineConfig, travel_model: TravelTimeModel, cost: CostModel | None = None):
        self.cfg = cfg
        self.travel_model = travel_model
        self.cost = cost or CostModel(cfg.cost)

    # ------------------------------------------------------------------ public API
    def optimize(
        self,
        now: float,
        orders: Sequence[Order],
        partners: Sequence[Partner],
        active_routes: Sequence[ActiveRoute] = (),
        route_orders: Mapping[str, Order] | None = None,
    ) -> DispatchDecision:
        started = time.perf_counter()
        opt = self.cfg.optimizer
        pending = sorted((o for o in orders if o.status == OrderStatus.PENDING), key=lambda o: (o.placed_at, o.id))
        idle = sorted(
            (p for p in partners if p.status == PartnerStatus.IDLE and p.available_at <= now + opt.epoch_min),
            key=lambda p: p.id,
        )
        partner_by_id = {p.id: p for p in partners}
        route_orders = dict(route_orders or {})

        oracle = TravelOracle(self.travel_model, self.cfg.travel, now)
        points = [o.pickup for o in pending] + [o.drop for o in pending] + [p.location for p in idle]
        for ar in active_routes:
            points.append(ar.partner_position)
            points.extend(s.location for s in ar.plan.stops)
        oracle.prime(points)
        ctx = _Context(
            now=now,
            oracle=oracle,
            compat=CompatibilityEngine(self.cfg, oracle),
            sequencer=RouteSequencer(self.cfg, oracle, self.cost),
            budget=FlexibilityBudget(self.cfg, oracle),
        )
        for o in pending:
            self._baseline(o, ctx)
        due = {o.id: ctx.budget.is_due(o, now) for o in pending}
        holdable = {o.id: ctx.budget.is_holdable(o, now) for o in pending}

        stats = OptimizationStats(
            n_orders=len(pending), n_partners=len(idle), n_active_routes=len(active_routes), solver=opt.solver
        )
        if not pending:
            stats.status = "NO_PENDING_ORDERS"
            stats.latency_ms = (time.perf_counter() - started) * 1000
            return DispatchDecision(stats=stats)

        screen_vehicle = self._screening_vehicle(
            [*idle, *(partner_by_id[a.plan.partner_id] for a in active_routes if a.plan.partner_id in partner_by_id)]
        )
        groups = self._generate_groups(pending, ctx, screen_vehicle)
        stats.n_groups = len(groups)

        columns: list[Column] = []
        if idle:
            columns.extend(self._new_columns(groups, idle, holdable, ctx))
        if opt.insertion_enabled and active_routes:
            columns.extend(self._insertion_columns(pending, active_routes, partner_by_id, route_orders, ctx))
        covered = {oid for c in columns for oid in c.new_order_ids}
        if idle:
            columns.extend(self._fallback_columns([o for o in pending if due[o.id] and o.id not in covered], idle, ctx))
        stats.n_columns = len(columns)

        chosen, status, objective = self._select(columns)
        stats.status = status
        stats.objective = objective

        decision = DispatchDecision(stats=stats)
        assigned: set[str] = set()
        for c in chosen:
            decision.routes.append(
                PlannedRoute(
                    kind=c.kind,
                    route=c.route,
                    value=c.value,
                    baseline_cost=c.baseline_cost,
                    replaces_route_id=c.replaces_route_id,
                    new_order_ids=c.new_order_ids,
                )
            )
            assigned.update(c.new_order_ids)
        for o in pending:
            if o.id in assigned:
                continue
            if due[o.id]:
                decision.unassigned_due_order_ids.append(o.id)
            elif holdable[o.id]:
                decision.held_order_ids.append(o.id)
            else:
                decision.deferred_order_ids.append(o.id)
        stats.latency_ms = (time.perf_counter() - started) * 1000
        return decision

    def _baseline(self, order: Order, ctx: _Context) -> float:
        """Solo-dispatch baseline cost of an order (cached per epoch)."""
        v = ctx.baseline.get(order.id)
        if v is None:
            approach = ctx.oracle.minutes_for_km(self.cfg.travel.approach_estimate_km)
            v = self.cost.solo_baseline(
                order.pickup, order.drop, ctx.oracle.minutes(order.pickup, order.drop), approach, self.cfg.travel
            )
            ctx.baseline[order.id] = v
        return v

    # ------------------------------------------------------------------ groups
    @staticmethod
    def _screening_vehicle(partners: Iterable[Partner]) -> Vehicle:
        vehicles = [p.vehicle for p in partners]
        if not vehicles:
            return Vehicle()
        return Vehicle(
            volume_l=max(v.volume_l for v in vehicles),
            weight_kg=max(v.weight_kg for v in vehicles),
            compartments=max(v.compartments for v in vehicles),
            insulated=any(v.insulated for v in vehicles),
        )

    def _generate_groups(self, pending: Sequence[Order], ctx: _Context, vehicle: Vehicle) -> list[tuple[Order, ...]]:
        opt = self.cfg.optimizer
        cp = self.cfg.compat
        rf = self.cfg.travel.road_factor
        groups: list[tuple[Order, ...]] = [(o,) for o in pending]
        if opt.max_group_size < 2 or len(pending) < 2:
            return groups

        compatible: dict[str, dict[str, float]] = defaultdict(dict)
        for i, a in enumerate(pending):
            for b in pending[i + 1 :]:
                if haversine_km(a.pickup, b.pickup) * rf > cp.max_pickup_distance_km:
                    continue
                if haversine_km(a.drop, b.drop) * rf > cp.max_drop_distance_km:
                    continue
                if abs(a.predicted_ready_at - b.predicted_ready_at) > cp.max_ready_gap_min:
                    continue
                r = ctx.compat.pair(a, b, vehicle)
                if r.feasible:
                    compatible[a.id][b.id] = r.score
                    compatible[b.id][a.id] = r.score

        by_id = {o.id: o for o in pending}
        top: dict[str, list[str]] = {
            oid: [k for k, _ in sorted(nbrs.items(), key=lambda kv: (-kv[1], kv[0]))[: opt.top_k_neighbors]]
            for oid, nbrs in compatible.items()
        }

        def score(ids: tuple[str, ...]) -> float:
            return min(compatible[a][b] for i, a in enumerate(ids) for b in ids[i + 1 :])

        level: set[tuple[str, ...]] = set()
        for oid, nbrs in top.items():
            for other in nbrs:
                level.add(tuple(sorted((oid, other))))
        budget = opt.max_candidate_groups - len(groups)
        ranked = sorted(level, key=lambda g: (-score(g), g))[: max(0, budget)]
        all_groups: list[tuple[str, ...]] = list(ranked)
        current = ranked
        size = 2
        while size < opt.max_group_size and current and len(all_groups) < budget:
            nxt: set[tuple[str, ...]] = set()
            for g in current:
                candidates = set().union(*(top.get(x, ()) for x in g)) - set(g)
                for c in candidates:
                    if all(c in compatible[x] for x in g):
                        nxt.add(tuple(sorted((*g, c))))
            remaining = budget - len(all_groups)
            current = sorted(nxt, key=lambda g: (-score(g), g))[: max(0, remaining)]
            all_groups.extend(current)
            size += 1
        groups.extend(tuple(by_id[i] for i in g) for g in all_groups)
        return groups

    # ------------------------------------------------------------------ columns
    def _nearest_partners(self, group: Sequence[Order], partners: Sequence[Partner]) -> list[Partner]:
        opt = self.cfg.optimizer
        rf = self.cfg.travel.road_factor
        scored = []
        for p in partners:
            d = min(haversine_km(p.location, o.pickup) for o in group) * rf
            if d <= opt.max_approach_km:
                scored.append((d, p.id, p))
        scored.sort(key=lambda t: (t[0], t[1]))
        k = opt.partners_per_group if len(group) < 3 else opt.partners_per_large_group
        return [p for _, _, p in scored[:k]]

    def _new_columns(
        self,
        groups: Sequence[tuple[Order, ...]],
        idle: Sequence[Partner],
        holdable: Mapping[str, bool],
        ctx: _Context,
    ) -> list[Column]:
        bonus = self.cfg.optimizer.assignment_bonus
        columns: list[Column] = []
        for group in groups:
            if len(group) == 1 and holdable[group[0].id]:
                continue
            for p in self._nearest_partners(group, idle):
                if len(group) > 1:
                    if not ctx.compat.group(group, p.vehicle).feasible:
                        continue
                elif handling_violations(group, p.vehicle, self.cfg):
                    continue
                route = ctx.sequencer.best_route(p.id, p.location, max(ctx.now, p.available_at), group)
                if route is None:
                    continue
                value = bonus * len(group) - route.objective
                if value <= 0:
                    continue
                columns.append(
                    Column(
                        kind=ColumnKind.NEW,
                        route=route,
                        value=value,
                        resources=(*(f"o:{o.id}" for o in group), f"p:{p.id}"),
                        baseline_cost=sum(ctx.baseline[o.id] for o in group),
                        new_order_ids=tuple(o.id for o in group),
                        priority=min(o.placed_at for o in group),
                    )
                )
        return columns

    def _insertion_columns(
        self,
        pending: Sequence[Order],
        active_routes: Sequence[ActiveRoute],
        partner_by_id: Mapping[str, Partner],
        route_orders: Mapping[str, Order],
        ctx: _Context,
    ) -> list[Column]:
        opt = self.cfg.optimizer
        rf = self.cfg.travel.road_factor
        columns: list[Column] = []
        for ar in active_routes:
            partner = partner_by_id.get(ar.plan.partner_id)
            if (
                partner is None
                or ar.started
                or ar.changes >= opt.max_route_changes
                or len(ar.plan.order_ids) >= opt.max_group_size
                or any(oid not in route_orders for oid in ar.plan.order_ids)
            ):
                continue
            existing = [route_orders[oid] for oid in ar.plan.order_ids]
            current = ctx.sequencer.best_route(partner.id, ar.partner_position, ctx.now, existing)
            if current is None:
                continue
            for o in pending:
                if (
                    min(haversine_km(o.pickup, e.pickup) for e in existing) * rf
                    > self.cfg.compat.max_pickup_distance_km
                ):
                    continue
                if not ctx.compat.group([*existing, o], partner.vehicle).feasible:
                    continue
                new = ctx.sequencer.best_route(partner.id, ar.partner_position, ctx.now, [*existing, o])
                if new is None:
                    continue
                delta = new.objective - current.objective
                if delta > ctx.baseline[o.id] - opt.insertion_min_gain:
                    continue
                columns.append(
                    Column(
                        kind=ColumnKind.INSERT,
                        route=new,
                        value=opt.assignment_bonus - delta,
                        resources=(f"o:{o.id}", f"r:{ar.route_id}"),
                        baseline_cost=sum(self._baseline(x, ctx) for x in (*existing, o)),
                        new_order_ids=(o.id,),
                        replaces_route_id=ar.route_id,
                        priority=o.placed_at,
                    )
                )
        return columns

    def _fallback_columns(self, orders: Sequence[Order], idle: Sequence[Partner], ctx: _Context) -> list[Column]:
        """Relaxed singleton columns for due orders with no feasible strict column."""
        bonus = self.cfg.optimizer.assignment_bonus
        columns: list[Column] = []
        for o in orders:
            candidates = (
                self._nearest_partners((o,), idle)
                or sorted(idle, key=lambda p: (haversine_km(p.location, o.pickup), p.id))[
                    : self.cfg.optimizer.partners_per_group
                ]
            )
            for p in candidates:
                if handling_violations((o,), p.vehicle, self.cfg):
                    continue
                route = ctx.sequencer.best_route(p.id, p.location, max(ctx.now, p.available_at), (o,), enforce=False)
                if route is None:
                    continue
                columns.append(
                    Column(
                        kind=ColumnKind.FALLBACK,
                        route=route,
                        value=max(1.0, bonus - route.objective),
                        resources=(f"o:{o.id}", f"p:{p.id}"),
                        baseline_cost=ctx.baseline[o.id],
                        new_order_ids=(o.id,),
                        priority=o.placed_at,
                    )
                )
        return columns

    # ------------------------------------------------------------------ selection
    def _select(self, columns: Sequence[Column]) -> tuple[list[Column], str, float]:
        if not columns:
            return [], "NO_COLUMNS", 0.0
        solver = self.cfg.optimizer.solver
        if solver == "fifo":
            chosen = fifo_select(columns)
            return chosen, "FIFO", sum(c.value for c in chosen)
        greedy = greedy_select(columns)
        if solver == "greedy":
            return greedy, "GREEDY", sum(c.value for c in greedy)
        try:
            solved, status = self._solve_cpsat(columns, greedy)
        except Exception:  # pragma: no cover - defensive: never block dispatch on solver failure
            log.exception("CP-SAT failed; falling back to greedy selection")
            return greedy, "GREEDY_FALLBACK", sum(c.value for c in greedy)
        if solved is None:
            return greedy, "GREEDY_FALLBACK", sum(c.value for c in greedy)
        return solved, status, sum(c.value for c in solved)

    def _solve_cpsat(self, columns: Sequence[Column], hint: Sequence[Column]) -> tuple[list[Column] | None, str]:
        from ortools.sat.python import cp_model

        opt = self.cfg.optimizer
        model = cp_model.CpModel()
        x = [model.new_bool_var(f"x{i}") for i in range(len(columns))]
        by_resource: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(columns):
            for r in c.resources:
                by_resource[r].append(i)
        for idxs in by_resource.values():
            if len(idxs) > 1:
                model.add_at_most_one(x[i] for i in idxs)
        model.maximize(sum(int(round(c.value * 100)) * x[i] for i, c in enumerate(columns)))
        hinted = {id(c) for c in hint}
        for i, c in enumerate(columns):
            model.add_hint(x[i], id(c) in hinted)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = opt.cpsat_time_limit_s
        solver.parameters.num_workers = max(1, opt.cpsat_workers)
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, solver.status_name(status)
        chosen = [c for i, c in enumerate(columns) if solver.boolean_value(x[i])]
        return chosen, solver.status_name(status)


def greedy_select(columns: Sequence[Column]) -> list[Column]:
    """Value-descending greedy set packing (1/k-approximation for k-set packing)."""
    used: set[str] = set()
    chosen: list[Column] = []
    for c in sorted(columns, key=lambda c: (-c.value, len(c.resources), c.priority)):
        if used.isdisjoint(c.resources):
            chosen.append(c)
            used.update(c.resources)
    return chosen


def fifo_select(columns: Sequence[Column]) -> list[Column]:
    """First-come-first-served: earliest order first, best column for it (independent dispatch)."""
    used: set[str] = set()
    chosen: list[Column] = []
    for c in sorted(columns, key=lambda c: (c.priority, c.kind == ColumnKind.FALLBACK, -c.value)):
        if used.isdisjoint(c.resources):
            chosen.append(c)
            used.update(c.resources)
    return chosen

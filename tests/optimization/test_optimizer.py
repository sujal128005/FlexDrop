from __future__ import annotations

import itertools
import random
from collections import Counter
from dataclasses import replace

import pytest

from flexdrop.config import EngineConfig
from flexdrop.domain.models import (
    ActiveRoute,
    ColumnKind,
    OrderStatus,
    PartnerStatus,
    StopType,
    Vehicle,
)
from flexdrop.engines.compatibility import handling_violations
from flexdrop.engines.cost import CostModel
from flexdrop.engines.flexibility import validate_allocation
from flexdrop.engines.optimizer import Column, DynamicCoordinationOptimizer, fifo_select, greedy_select
from flexdrop.engines.routing import RouteSequencer
from flexdrop.engines.travel import TravelOracle
from flexdrop.geo import offset
from tests.conftest import CENTER, T0, level


def _random_instance(make_order, make_partner, seed: int, n_orders: int = 14, n_partners: int = 6):  # noqa: ANN001, ANN202
    rnd = random.Random(seed)
    orders = []
    for _ in range(n_orders):
        store = offset(CENTER, rnd.uniform(-1.2, 1.2), rnd.uniform(-1.2, 1.2))
        drop = offset(CENTER, rnd.uniform(-1.0, 1.0) + 1.5, rnd.uniform(-1.0, 1.0))
        ready = rnd.uniform(-2, 8)
        orders.append(
            make_order(pickup=store, drop=drop, ready=ready, promised=ready + 28, flex=rnd.choice([0, 0, 10]))
        )
    partners = [
        make_partner(f"p{i}", offset(CENTER, rnd.uniform(-1.5, 1.5), rnd.uniform(-1.5, 1.5))) for i in range(n_partners)
    ]
    return orders, partners


@pytest.mark.parametrize("seed", range(5))
def test_plan_invariants(cfg, travel_model, make_order, make_partner, seed) -> None:  # noqa: ANN001
    orders, partners = _random_instance(make_order, make_partner, seed)
    decision = DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, orders, partners)
    by_id = {o.id: o for o in orders}
    order_use = Counter(oid for r in decision.routes for oid in r.route.order_ids)
    partner_use = Counter(r.route.partner_id for r in decision.routes)
    assert all(v == 1 for v in order_use.values()), "an order was assigned twice"
    assert all(v == 1 for v in partner_use.values()), "a partner received two routes"
    accounted = (
        set(order_use)
        | set(decision.held_order_ids)
        | set(decision.deferred_order_ids)
        | set(decision.unassigned_due_order_ids)
    )
    assert accounted == set(by_id)
    for pr in decision.routes:
        route_orders = [by_id[oid] for oid in pr.route.order_ids]
        assert not handling_violations(route_orders, Vehicle(), cfg)
        assert len(route_orders) <= cfg.optimizer.max_group_size
        if pr.kind != ColumnKind.FALLBACK:
            validate_allocation(pr.route, route_orders)
            assert not pr.route.is_late
        seen: set[str] = set()
        for s in pr.route.stops:
            if s.type == StopType.PICKUP:
                seen.add(s.order_id)
            else:
                assert s.order_id in seen
    assert decision.stats.status in ("OPTIMAL", "FEASIBLE", "NO_COLUMNS")


def _brute_force(columns: list[Column]) -> float:
    best = 0.0
    for k in range(1, len(columns) + 1):
        for subset in itertools.combinations(columns, k):
            res = [r for c in subset for r in c.resources]
            if len(res) == len(set(res)):
                best = max(best, sum(c.value for c in subset))
    return best


@pytest.mark.parametrize("seed", range(8))
def test_cpsat_set_packing_is_optimal(cfg, travel_model, make_order, seed) -> None:  # noqa: ANN001
    rnd = random.Random(seed)
    seq = RouteSequencer(cfg, TravelOracle(travel_model, cfg.travel, T0), CostModel(cfg.cost))
    dummy = seq.best_route("p", CENTER, T0, [make_order(promised=60)])
    assert dummy is not None
    resources = [f"o:{i}" for i in range(6)] + [f"p:{i}" for i in range(3)]
    columns = []
    for _ in range(12):
        res = tuple(sorted(set(rnd.sample(resources[:6], rnd.randint(1, 3))) | {rnd.choice(resources[6:])}))
        columns.append(Column(ColumnKind.NEW, dummy, rnd.uniform(1, 100), res, 0.0, ()))
    opt = DynamicCoordinationOptimizer(cfg, travel_model)
    chosen, status, objective = opt._select(columns)
    assert status == "OPTIMAL"
    assert objective == pytest.approx(_brute_force(columns), abs=0.02)
    greedy_value = sum(c.value for c in greedy_select(columns))
    assert greedy_value <= objective + 1e-9
    used = [r for c in chosen for r in c.resources]
    assert len(used) == len(set(used))
    fifo = fifo_select(columns)
    fifo_res = [r for c in fifo for r in c.resources]
    assert len(fifo_res) == len(set(fifo_res))


def _two_order_case(make_order, flex: float, oracle):  # noqa: ANN001, ANN202
    drop = offset(CENTER, 1.6, 0.2)
    trip = oracle.minutes(CENTER, drop)
    a = make_order(oid="A", drop=drop, ready=-8, promised=3 + trip, flex=flex)
    b = make_order(oid="B", drop=drop, ready=4, promised=12 + trip)
    return a, b


def test_flexibility_enables_coordination(cfg, travel_model, oracle, make_order, make_partner) -> None:  # noqa: ANN001
    partners = [make_partner("p1"), make_partner("p2")]
    opt = DynamicCoordinationOptimizer(cfg, travel_model)
    standard = opt.optimize(T0, list(_two_order_case(make_order, 0.0, oracle)), partners)
    assert sorted(len(r.route.order_ids) for r in standard.routes) == [1, 1]
    flexible = opt.optimize(T0, list(_two_order_case(make_order, 10.0, oracle)), partners)
    assert [len(r.route.order_ids) for r in flexible.routes] == [2]
    route = flexible.routes[0].route
    assert 0 < route.flex_used["A"] <= 10.0
    assert flexible.routes[0].expected_savings > 0
    total_std = sum(r.route.cost.total for r in standard.routes)
    assert route.cost.total < total_std


def test_flex_order_is_held_then_dispatched_when_due(cfg, travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    opt = DynamicCoordinationOptimizer(cfg, travel_model)
    o = make_order(oid="H", ready=2, promised=35, flex=15)
    d1 = opt.optimize(T0, [o], [make_partner("p1")])
    assert d1.held_order_ids == ["H"] and not d1.routes
    later = T0 + 20
    d2 = opt.optimize(later, [o], [replace(make_partner("p1"), available_at=later)])
    assert [r.route.order_ids for r in d2.routes] == [("H",)]


def test_fallback_for_due_orders_without_feasible_column(cfg, travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    o = make_order(oid="LATE", ready=0, promised=6)
    d = DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, [o], [make_partner("far", offset(CENTER, 3, 3))])
    assert len(d.routes) == 1 and d.routes[0].kind == ColumnKind.FALLBACK
    assert d.routes[0].route.is_late


def test_no_partners_means_no_routes(cfg, travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    p = replace(make_partner("busy"), status=PartnerStatus.ASSIGNED)
    o = make_order(ready=0, promised=30)
    d = DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, [o], [p])
    assert not d.routes and d.stats.n_partners == 0
    d2 = DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, [], [p])
    assert d2.stats.status == "NO_PENDING_ORDERS"
    o.status = OrderStatus.CANCELLED
    assert DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, [o], [p]).stats.n_orders == 0


def test_insertion_into_unstarted_route(travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    cfg = EngineConfig.for_level(3, cpsat_workers=1)
    existing = make_order(oid="E", ready=6, promised=40)
    existing.status = OrderStatus.PLANNED
    partner = replace(make_partner("p1", offset(CENTER, 0.3, 0.3)), status=PartnerStatus.ASSIGNED)
    seq = RouteSequencer(cfg, TravelOracle(travel_model, cfg.travel, T0), CostModel(cfg.cost))
    plan = seq.best_route("p1", partner.location, T0, [existing])
    assert plan is not None
    active = ActiveRoute("R1", plan, partner.location, T0)
    new = make_order(oid="N", ready=7, promised=42)
    d = DynamicCoordinationOptimizer(cfg, travel_model).optimize(T0, [new], [partner], [active], {"E": existing})
    assert len(d.routes) == 1
    pr = d.routes[0]
    assert pr.kind == ColumnKind.INSERT and pr.replaces_route_id == "R1"
    assert set(pr.route.order_ids) == {"E", "N"} and pr.new_order_ids == ("N",)
    assert pr.route.delivery_at["E"] <= existing.latest_delivery_at + 1e-6


def test_independent_fifo_policy_never_batches(cfg, travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    fifo = level(cfg, max_group_size=1, hold_enabled=False, solver="fifo")
    orders, partners = _random_instance(make_order, make_partner, 3)
    d = DynamicCoordinationOptimizer(fifo, travel_model).optimize(T0, orders, partners)
    assert all(len(r.route.order_ids) == 1 for r in d.routes)
    assert d.stats.status == "FIFO"


def test_greedy_solver_option(cfg, travel_model, make_order, make_partner) -> None:  # noqa: ANN001
    greedy = level(cfg, solver="greedy")
    orders, partners = _random_instance(make_order, make_partner, 4)
    d = DynamicCoordinationOptimizer(greedy, travel_model).optimize(T0, orders, partners)
    assert d.stats.status in ("GREEDY", "NO_COLUMNS")

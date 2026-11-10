from __future__ import annotations

import itertools
import random

import pytest

from flexdrop.domain.models import DeliveryMode, StopType
from flexdrop.engines.flexibility import (
    FlexibilityBudget,
    FlexibilityViolation,
    flexibility_utilisation,
    validate_allocation,
)
from flexdrop.engines.routing import RouteSequencer
from flexdrop.geo import offset
from tests.conftest import CENTER, T0


def _valid_sequences(orders, picked=frozenset()):  # noqa: ANN001, ANN202
    stops = [(o.id, StopType.PICKUP) for o in orders if o.id not in picked] + [(o.id, StopType.DROP) for o in orders]
    for perm in itertools.permutations(stops):
        seen = set(picked)
        ok = True
        for oid, t in perm:
            if t == StopType.PICKUP:
                seen.add(oid)
            elif oid not in seen:
                ok = False
                break
        if ok:
            yield list(perm)


def test_single_order_timing_is_exact(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    o = make_order(ready=0, promised=60)
    seq = RouteSequencer(cfg, oracle, cost)
    start = offset(CENTER, 0.5, 0.5)
    r = seq.best_route("p", start, T0, [o])
    assert r is not None
    approach = oracle.minutes(start, o.pickup)
    trip = oracle.minutes(o.pickup, o.drop)
    pick_dep = max(T0 + approach, o.predicted_ready_at) + cfg.travel.pickup_service_min
    assert r.stops[0].type == StopType.PICKUP and r.stops[1].type == StopType.DROP
    assert r.delivery_at[o.id] == pytest.approx(pick_dep + trip)
    assert r.distance_km == pytest.approx(oracle.km(start, o.pickup) + oracle.km(o.pickup, o.drop))
    assert r.cost.total == pytest.approx(cost.route_cost(r.distance_km, r.duration_min, 1).total)


@pytest.mark.parametrize("seed", range(6))
def test_branch_and_bound_matches_brute_force(cfg, oracle, cost, make_order, seed) -> None:  # noqa: ANN001
    rnd = random.Random(seed)
    orders = [
        make_order(
            pickup=offset(CENTER, rnd.uniform(-0.8, 0.8), rnd.uniform(-0.8, 0.8)),
            drop=offset(CENTER, rnd.uniform(0.5, 1.8), rnd.uniform(-0.8, 0.8)),
            ready=rnd.uniform(0, 6),
            promised=rnd.uniform(35, 55),
            flex=rnd.choice([0, 10]),
        )
        for _ in range(3)
    ]
    seq = RouteSequencer(cfg, oracle, cost)
    best = seq.best_route("p", CENTER, T0, orders)
    brute = [seq.evaluate_sequence("p", CENTER, T0, orders, s) for s in _valid_sequences(orders)]
    feasible = [b for b in brute if b is not None]
    if not feasible:
        assert best is None
        return
    assert best is not None
    assert best.objective == pytest.approx(min(b.objective for b in feasible), rel=1e-9)


def test_constraints_hold_in_returned_route(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    orders = [make_order(ready=i, promised=45 + i, flex=10) for i in range(3)]
    r = RouteSequencer(cfg, oracle, cost).best_route("p", CENTER, T0, orders)
    assert r is not None
    seen: set[str] = set()
    for s in r.stops:
        if s.type == StopType.PICKUP:
            seen.add(s.order_id)
            assert s.wait <= cfg.optimizer.max_store_wait_min + 1e-6
        else:
            assert s.order_id in seen
    for o in orders:
        assert r.delivery_at[o.id] <= o.latest_delivery_at + 1e-6
        assert 0 <= r.flex_used[o.id] <= o.max_extra_wait + 1e-6
    validate_allocation(r, orders)


def test_infeasible_and_relaxed_routing(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    o = make_order(ready=0, promised=1)  # impossible commitment
    seq = RouteSequencer(cfg, oracle, cost)
    assert seq.best_route("p", CENTER, T0, [o]) is None
    relaxed = seq.best_route("p", CENTER, T0, [o], enforce=False)
    assert relaxed is not None and relaxed.is_late and relaxed.lateness[o.id] > 0
    far_ready = make_order(ready=30, promised=80)
    assert seq.best_route("p", CENTER, T0, [far_ready]) is None  # partner would wait > max_store_wait


def test_picked_orders_only_need_drop(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    a, b = make_order(promised=40), make_order(ready=2, promised=45)
    r = RouteSequencer(cfg, oracle, cost).best_route("p", CENTER, T0, [a, b], picked=frozenset({a.id}))
    assert r is not None
    assert [(s.order_id, s.type) for s in r.stops].count((a.id, StopType.PICKUP)) == 0
    assert len(r.stops) == 3


def test_evaluate_sequence_rejects_invalid(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    o = make_order()
    seq = RouteSequencer(cfg, oracle, cost)
    assert seq.evaluate_sequence("p", CENTER, T0, [o], [(o.id, StopType.DROP), (o.id, StopType.PICKUP)]) is None
    assert seq.evaluate_sequence("p", CENTER, T0, [o], [(o.id, StopType.PICKUP)]) is None


def test_route_size_limits(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    seq = RouteSequencer(cfg, oracle, cost)
    assert seq.best_route("p", CENTER, T0, []) is None
    with pytest.raises(ValueError):
        seq.best_route("p", CENTER, T0, [make_order() for _ in range(7)])
    o = make_order()
    with pytest.raises(ValueError):
        seq.best_route("p", CENTER, T0, [o, o])


def test_dispatch_deadline_and_holds(cfg, oracle, make_order) -> None:  # noqa: ANN001
    budget = FlexibilityBudget(cfg, oracle)
    o = make_order(ready=5, promised=40, flex=10)
    trip = oracle.minutes(o.pickup, o.drop)
    approach = oracle.minutes_for_km(cfg.travel.approach_estimate_km)
    expected = (
        min(o.latest_delivery_at, o.predicted_ready_at + o.planning_age_limit(cfg.quality))
        - trip
        - cfg.travel.pickup_service_min
        - approach
        - cfg.optimizer.hold_safety_min
    )
    assert budget.dispatch_deadline(o) == pytest.approx(expected)
    assert budget.is_holdable(o, T0)
    assert not budget.is_due(o, T0)
    assert budget.is_due(o, expected)
    assert not budget.is_holdable(o, expected)
    standard = make_order(ready=5, promised=40)
    assert standard.mode == DeliveryMode.STANDARD and not budget.is_holdable(standard, T0)


def test_validate_allocation_and_utilisation(cfg, oracle, cost, make_order) -> None:  # noqa: ANN001
    orders = [make_order(ready=0, promised=20, flex=15)]
    r = RouteSequencer(cfg, oracle, cost).best_route("p", CENTER, T0, orders)
    assert r is not None
    validate_allocation(r, orders)
    u = flexibility_utilisation([r], orders)
    assert 0.0 <= u <= 1.0
    shrunk = [make_order(oid=orders[0].id, ready=0, promised=20, flex=0)]
    if r.flex_used[orders[0].id] > 0:
        with pytest.raises(FlexibilityViolation):
            validate_allocation(r, shrunk)

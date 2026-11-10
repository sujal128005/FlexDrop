from __future__ import annotations

from dataclasses import replace

import pytest

from flexdrop.config import EngineConfig
from flexdrop.domain.models import Category, TempClass, Vehicle
from flexdrop.engines.compatibility import CompatibilityEngine, handling_violations
from flexdrop.engines.cost import CostModel
from flexdrop.geo import offset
from tests.conftest import CENTER


def test_route_cost_formula(cost: CostModel) -> None:
    c = cost.route_cost(distance_km=4.0, engaged_min=30.0, n_orders=2)
    p = cost.p
    pay = p.base_route_fee + p.per_km * 4 + p.per_engaged_minute * 30 + p.extra_drop_fee
    assert c.partner_pay == pytest.approx(pay)
    assert c.earnings_top_up == 0.0
    assert c.total == pytest.approx(pay + p.coordination_overhead_per_extra_order + p.platform_overhead_per_route)


def test_earnings_floor_top_up(cost: CostModel) -> None:
    c = cost.route_cost(distance_km=0.1, engaged_min=60.0, n_orders=1)
    assert c.earnings_top_up > 0
    assert c.partner_pay == pytest.approx(cost.p.min_partner_hourly_earnings)


def test_cost_rejects_negative(cost: CostModel) -> None:
    with pytest.raises(ValueError):
        cost.route_cost(-1, 10, 1)


def test_marginal_cost(cost: CostModel) -> None:
    p = cost.p
    assert cost.marginal_order_cost(1.0, 5.0) == pytest.approx(
        p.extra_drop_fee + p.coordination_overhead_per_extra_order + p.per_km + 5 * p.per_engaged_minute
    )


def test_same_store_orders_compatible(cfg, oracle, make_order) -> None:  # noqa: ANN001
    eng = CompatibilityEngine(cfg, oracle)
    a, b = make_order(), make_order(ready=3)
    r = eng.pair(a, b, Vehicle())
    assert r.feasible and 0 < r.score <= 1


def test_cross_category_requires_level_2(cfg, oracle, make_order) -> None:  # noqa: ANN001
    a = make_order()
    g = make_order(category=Category.GROCERY, temps=frozenset({TempClass.AMBIENT}))
    assert "cross_category_disabled" in CompatibilityEngine(cfg, oracle).pair(a, g, Vehicle()).reasons
    l2 = EngineConfig.for_level(2)
    assert CompatibilityEngine(l2, oracle).pair(a, g, Vehicle()).feasible


def test_thermal_segregation(cfg, make_order) -> None:  # noqa: ANN001
    l2 = EngineConfig.for_level(2)
    hot = make_order()
    frozen = make_order(category=Category.GROCERY, temps=frozenset({TempClass.FROZEN}))
    assert handling_violations([hot, frozen], Vehicle(), l2) == []
    assert "thermal_segregation_unavailable" in handling_violations([hot, frozen], Vehicle(compartments=1), l2)
    assert "thermal_segregation_unavailable" in handling_violations([hot, frozen], Vehicle(insulated=False), l2)


def test_contamination_leak_fragile_capacity(cfg, make_order) -> None:  # noqa: ANN001
    l2 = EngineConfig.for_level(2)
    food = make_order(temps=frozenset({TempClass.AMBIENT}), sealed=False)
    meat = make_order(category=Category.GROCERY, temps=frozenset({TempClass.CHILLED}), raw_meat=True)
    assert "raw_meat_contamination" in handling_violations([food, meat], Vehicle(), l2)
    leaky = make_order(category=Category.GROCERY, temps=frozenset({TempClass.AMBIENT}), leak_risk=True, sealed=False)
    assert "leak_risk" in handling_violations([leaky, make_order()], Vehicle(), l2)
    fragile = make_order(fragile=True)
    heavy = make_order(weight=8.0)
    assert "fragile_crush_risk" in handling_violations([fragile, heavy], Vehicle(), cfg)
    big = [make_order(volume=30.0), make_order(volume=30.0)]
    assert "volume_capacity" in handling_violations(big, Vehicle(), cfg)
    heavy2 = [make_order(weight=9.0), make_order(weight=9.0)]
    assert "weight_capacity" in handling_violations(heavy2, Vehicle(), cfg)


def test_spatial_and_temporal_rules(cfg, oracle, make_order) -> None:  # noqa: ANN001
    eng = CompatibilityEngine(cfg, oracle)
    far_pickup = make_order(pickup=offset(CENTER, 0, 5))
    assert "pickup_too_far" in eng.pair(make_order(), far_pickup, Vehicle()).reasons
    far_drop = make_order(drop=offset(CENTER, -4, 0))
    assert "drop_too_far" in eng.pair(make_order(), far_drop, Vehicle()).reasons
    late = make_order(ready=30)
    assert "ready_gap_too_large" in eng.pair(make_order(), late, Vehicle()).reasons
    opposite = make_order(drop=offset(CENTER, -1.5, 0))
    assert "detour_too_large" in eng.pair(make_order(), opposite, Vehicle()).reasons


def test_same_store_only_restriction(cfg, oracle, make_order) -> None:  # noqa: ANN001
    strict = replace(cfg, compat=replace(cfg.compat, same_store_only=True))
    eng = CompatibilityEngine(strict, oracle)
    other_store = make_order(pickup=offset(CENTER, 0.6, 0))
    assert "not_same_store" in eng.pair(make_order(), other_store, Vehicle()).reasons
    assert eng.pair(make_order(), make_order(), Vehicle()).feasible


def test_group_requires_all_pairs(cfg, oracle, make_order) -> None:  # noqa: ANN001
    eng = CompatibilityEngine(cfg, oracle)
    a, b, c = make_order(), make_order(), make_order(ready=30)
    assert eng.group([a, b], Vehicle()).feasible
    assert not eng.group([a, b, c], Vehicle()).feasible

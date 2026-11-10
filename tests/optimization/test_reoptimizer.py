from __future__ import annotations

from dataclasses import replace

from flexdrop.domain.models import StopType
from flexdrop.engines.reoptimizer import ReoptAction, ReoptimizationEngine
from flexdrop.geo import offset
from tests.conftest import CENTER, T0


def test_unchanged_when_current_sequence_still_good(cfg, travel_model, make_order) -> None:  # noqa: ANN001
    eng = ReoptimizationEngine(cfg, travel_model)
    a, b = make_order(oid="A", promised=45), make_order(oid="B", ready=2, promised=48)
    first = eng.replan(T0, "p", CENTER, T0, [a, b], frozenset())
    assert first.action == ReoptAction.RESEQUENCED and first.route is not None
    seq = [(s.order_id, s.type) for s in first.route.stops]
    again = eng.replan(T0, "p", CENTER, T0, [a, b], frozenset(), current_sequence=seq)
    assert again.action == ReoptAction.UNCHANGED


def test_prep_delay_splits_route_and_releases_order(cfg, travel_model, make_order) -> None:  # noqa: ANN001
    eng = ReoptimizationEngine(cfg, travel_model)
    a = make_order(oid="A", promised=30)
    b = make_order(oid="B", ready=2, promised=32)
    delayed_b = replace(b, predicted_ready_at=T0 + 25, ready_p90_at=T0 + 27)
    result = eng.replan(T0, "p", CENTER, T0, [a, delayed_b], frozenset())
    assert result.action == ReoptAction.SPLIT
    assert result.released_order_ids == ["B"]
    assert result.route is not None and result.route.order_ids == ("A",)


def test_on_board_orders_never_released(cfg, travel_model, make_order) -> None:  # noqa: ANN001
    eng = ReoptimizationEngine(cfg, travel_model)
    a = make_order(oid="A", promised=1)  # already on board and now impossible to deliver on time
    b = make_order(oid="B", ready=1, promised=2)
    result = eng.replan(T0 + 5, "p", offset(CENTER, 2, 2), T0 + 5, [a, b], frozenset({"A"}))
    assert result.action == ReoptAction.AT_RISK
    assert result.released_order_ids == ["B"]
    assert result.at_risk_order_ids == ["A"]
    assert result.route is not None and result.route.order_ids == ("A",)
    assert [s.type for s in result.route.stops] == [StopType.DROP]


def test_release_all_when_nothing_on_board(cfg, travel_model, make_order) -> None:  # noqa: ANN001
    eng = ReoptimizationEngine(cfg, travel_model)
    a = make_order(oid="A", promised=1)
    result = eng.replan(T0, "p", CENTER, T0, [a], frozenset())
    assert result.action == ReoptAction.RELEASED_ALL and result.released_order_ids == ["A"]


def test_completed_and_partner_unavailable(cfg, travel_model, make_order) -> None:  # noqa: ANN001
    eng = ReoptimizationEngine(cfg, travel_model)
    assert eng.replan(T0, "p", CENTER, T0, [], frozenset()).action == ReoptAction.COMPLETED
    a, b = make_order(oid="A"), make_order(oid="B")
    r = eng.partner_unavailable([a, b], frozenset({"A"}))
    assert r.action == ReoptAction.AT_RISK and r.released_order_ids == ["B"] and r.at_risk_order_ids == ["A"]
    r2 = eng.partner_unavailable([b], frozenset())
    assert r2.action == ReoptAction.RELEASED_ALL

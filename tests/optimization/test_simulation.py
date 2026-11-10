from __future__ import annotations

import json

import pytest

from flexdrop.config import TravelParams
from flexdrop.ml.registry import analytic_bundle
from flexdrop.simulation.export import export_scenario
from flexdrop.simulation.policies import POLICIES, get_policy
from flexdrop.simulation.runner import full_pipeline, render_markdown, run_policies
from flexdrop.simulation.scenario import build_scenario, campus_spec, city_spec, demand_rate
from flexdrop.simulation.simulator import Simulator


@pytest.fixture(scope="module")
def scenario():  # noqa: ANN201
    return build_scenario(campus_spec(seed=11, duration_min=45))


@pytest.fixture(scope="module")
def results(scenario):  # noqa: ANN001, ANN201
    models = analytic_bundle(TravelParams())
    return {name: Simulator(scenario, get_policy(name), models, seed=1).run() for name in POLICIES}


def test_scenario_is_reproducible() -> None:
    a = build_scenario(campus_spec(seed=5, duration_min=20))
    b = build_scenario(campus_spec(seed=5, duration_min=20))
    assert [o.id for o in a.orders] == [o.id for o in b.orders]
    assert [o.true_prep_min for o in a.orders] == [o.true_prep_min for o in b.orders]
    c = build_scenario(city_spec(seed=5, duration_min=20))
    assert c.spec.name == "city" and len(c.stores) == 64
    assert demand_rate(campus_spec(), 21.5) == pytest.approx(2.6)
    assert demand_rate(campus_spec(), 0.5) == pytest.approx(0.9)


def test_every_order_is_accounted_for(results, scenario) -> None:  # noqa: ANN001
    for name, r in results.items():
        m = r.metrics
        assert m["orders_total"] == len(scenario.orders), name
        assert m["orders_delivered"] + m["orders_cancelled"] + m["orders_undelivered"] == m["orders_total"]
        assert m["orders_undelivered"] == 0, name
        assert 0.0 <= m["on_time_rate"] <= 1.0
        assert m["fulfilment_cost_per_order"] > 0


def test_policy_behaviour(results) -> None:  # noqa: ANN001
    ind = results["independent"].metrics
    flex = results["flexdrop"].metrics
    assert ind["avg_orders_per_route"] <= 1.0 + 1e-9
    assert ind["coordinated_share"] == 0.0
    assert results["batching"].metrics["acceptance_rate"] == 0.0
    assert flex["offers_shown"] > 0 and 0 < flex["acceptance_rate"] <= 1
    assert flex["coordinated_share"] > ind["coordinated_share"]
    assert flex["fulfilment_cost_per_order"] < ind["fulfilment_cost_per_order"]
    assert flex["flex_utilisation"] <= 1.0
    assert results["flexdrop_l3"].metrics["insertions"] >= 0


def test_simulation_is_deterministic(scenario) -> None:  # noqa: ANN001
    models = analytic_bundle(TravelParams())
    a = Simulator(scenario, get_policy("flexdrop"), models, seed=3).run().metrics
    b = Simulator(scenario, get_policy("flexdrop"), models, seed=3).run().metrics
    for key in ("orders_delivered", "routes", "fulfilment_cost_per_order", "on_time_rate", "flex_accepted"):
        assert a[key] == b[key]


def test_coordination_samples_and_records(results) -> None:  # noqa: ANN001
    r = results["flexdrop"]
    assert r.coordination_samples and {"coordinated", "n_near_both"} <= set(r.coordination_samples[0])
    assert {"order_id", "on_time", "route_size"} <= set(r.orders[0])
    assert r.epochs and "latency_ms" in r.epochs[0]


def test_unknown_policy() -> None:
    with pytest.raises(ValueError):
        get_policy("nope")


def test_runner_and_export(tmp_path, scenario) -> None:  # noqa: ANN001
    out = run_policies(scenario, ["independent", "flexdrop"], tmp_path / "no-models", tmp_path / "reports")
    assert set(out) == {"independent", "flexdrop"}
    data = json.loads((tmp_path / "reports" / "campus_metrics.json").read_text())
    assert data["flexdrop"]["policy"] == "flexdrop"
    md = render_markdown("campus", out)
    assert "Relative change" in md and "`flexdrop`" in md
    paths = export_scenario(scenario, tmp_path / "export")
    assert all(p.exists() for p in paths.values())


@pytest.mark.slow
def test_full_pipeline_small(tmp_path) -> None:  # noqa: ANN001
    results = full_pipeline(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "reports",
        scenarios=["campus"],
        policies=["independent", "flexdrop"],
        duration_min=40,
    )
    assert (tmp_path / "models" / "metadata.json").exists()
    assert (tmp_path / "models" / "coordination_model.joblib").exists()
    assert (tmp_path / "reports" / "SUMMARY.md").exists()
    assert results["campus"]["flexdrop"]["orders_delivered"] > 0


def test_seed_robustness_report(tmp_path) -> None:  # noqa: ANN001
    from flexdrop.simulation.runner import seed_robustness

    summary = seed_robustness(
        tmp_path / "no-models", tmp_path / "r", ["campus"], ["independent", "flexdrop"], [1, 2], duration_min=20
    )
    assert set(summary["campus"]) == {"independent", "flexdrop"}
    assert summary["campus"]["independent"]["cost_change_pct_mean"] == 0.0
    assert (tmp_path / "r" / "ROBUSTNESS.md").exists()

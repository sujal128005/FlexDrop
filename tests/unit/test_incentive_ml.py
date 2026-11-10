from __future__ import annotations

import numpy as np
import pytest

from flexdrop.config import TravelParams
from flexdrop.domain.models import GeoPoint
from flexdrop.engines.incentive import CustomerContext, DensityContext, IncentiveOptimizer
from flexdrop.engines.promise import compute_density, standard_promise
from flexdrop.geo import offset
from flexdrop.ml.features import AcceptanceFeatures, CoordinationFeatures, PrepFeatures
from flexdrop.ml.models import AnalyticAcceptance, AnalyticCoordination, AnalyticPrepTime, AnalyticTravelTime
from flexdrop.ml.registry import ARTEFACTS, analytic_bundle, load_bundle
from flexdrop.ml.training import train_all, write_coordination_samples
from flexdrop.simulation.history import generate_history
from flexdrop.simulation.scenario import build_scenario, campus_spec
from tests.conftest import CENTER, T0


@pytest.fixture
def bundle():  # noqa: ANN201
    return analytic_bundle(TravelParams())


def test_quote_respects_economic_constraints(cfg, oracle, make_order, bundle) -> None:  # noqa: ANN001
    eio = IncentiveOptimizer(cfg, bundle)
    o = make_order(ready=10, promised=30)
    offers = eio.quote(o, CustomerContext(0.6, True), DensityContext(8, 10, 6), oracle, 20.0)
    assert offers, "dense area should produce offers"
    s_bar = eio.attributable_savings(o, oracle)
    fp = cfg.flex
    for off in offers:
        assert off.extra_minutes in fp.wait_options_min
        assert fp.reward_min <= off.reward <= fp.reward_max
        assert off.reward <= fp.max_reward_share * off.expected_savings + 1e-9
        assert off.expected_savings - off.reward >= fp.min_expected_margin - 1e-9
        assert off.expected_savings == pytest.approx(off.coordination_probability * s_bar, rel=1e-3)
        assert off.expected_net_value == pytest.approx(
            off.acceptance_probability * (off.expected_savings - off.reward), rel=1e-3
        )
        assert off.flex_eta_at == pytest.approx(o.promised_at + off.extra_minutes)


def test_no_offer_without_coordination_potential(cfg, oracle, make_order, bundle) -> None:  # noqa: ANN001
    eio = IncentiveOptimizer(cfg, bundle)
    o = make_order(ready=10, promised=30)
    assert eio.quote(o, CustomerContext(), DensityContext(0, 0, 0), oracle, 20.0) == []


def test_quality_guard_limits_wait_options(cfg, oracle, make_order, bundle) -> None:  # noqa: ANN001
    eio = IncentiveOptimizer(cfg, bundle)
    tight = make_order(ready=0, promised=27)  # hot food: limit = 40 - 5 - 5 = 30 min after ready
    assert eio.allowed_wait_options(tight, oracle) == []
    relaxed = make_order(ready=0, promised=10)
    assert eio.allowed_wait_options(relaxed, oracle) == [5, 10, 15]


def test_density_and_promise(cfg) -> None:  # noqa: ANN001
    near = (offset(CENTER, 0.2, 0), offset(CENTER, 1.2, 0))
    far = (offset(CENTER, 5, 0), offset(CENTER, 6, 0))
    d = compute_density(CENTER, offset(CENTER, 1.0, 0), [near, near, far], 1.0)
    assert (d.n_near_pickup, d.n_near_drop, d.n_near_both) == (2, 2, 2)
    p = standard_promise(100.0, prep_min := 15.0, 10.0, 5.0, cfg)
    assert p == pytest.approx(100 + prep_min + cfg.travel.pickup_service_min + 10 + cfg.promise.eta_buffer_min)
    assert standard_promise(100.0, 2.0, 10.0, 5.0, cfg) == pytest.approx(
        105 + cfg.travel.pickup_service_min + 10 + cfg.promise.eta_buffer_min
    )


def test_analytic_priors_behave() -> None:
    mean, p90 = AnalyticPrepTime().predict([PrepFeatures(True, 12, 2, 20, False, 3)])
    assert p90[0] > mean[0] > 0
    t = AnalyticTravelTime(TravelParams()).predict_minutes(np.array([0.0, 2.0, 4.0]), T0)
    assert t[0] == 0 and t[2] > t[1] > 0
    acc = AnalyticAcceptance().predict_proba([AcceptanceFeatures(r, 10, 0.5, True, 400, 20) for r in (0, 10, 20)])
    assert acc[0] < acc[1] < acc[2]
    q = AnalyticCoordination().predict_proba(
        [CoordinationFeatures(m, 3, 3, 3, 20, True) for m in (0, 10)] + [CoordinationFeatures(0, 0, 0, 0, 20, True)]
    )
    assert q[1] > q[0] >= q[2] >= 0.02


@pytest.fixture(scope="module")
def trained(tmp_path_factory):  # noqa: ANN001, ANN201
    root = tmp_path_factory.mktemp("ml")
    sc = build_scenario(campus_spec(duration_min=30))
    generate_history(sc, root / "data", n_prep=3000, n_trips=3000, n_offers=4000, seed=3)
    rng = np.random.default_rng(0)
    samples = []
    for _ in range(600):
        n_both = int(rng.integers(0, 10))
        m = float(rng.choice([0, 5, 10, 15]))
        p = 1 - np.exp(-(0.1 * n_both) * (1 + m / 10))
        samples.append(
            {
                "extra_min": m,
                "n_near_pickup": n_both + 2,
                "n_near_drop": n_both + 1,
                "n_near_both": n_both,
                "hour": 20.0,
                "is_food": 1,
                "coordinated": int(rng.random() < p),
            }
        )
    write_coordination_samples(samples, root / "data" / "coordination_samples.csv")
    metrics = train_all(root / "data", root / "models", seed=3)
    return root, metrics


def test_training_beats_or_matches_priors(trained) -> None:  # noqa: ANN001
    _, m = trained
    assert m["prep"]["mae_min"] <= m["prep"]["prior_mae_min"] * 1.05
    assert 0.75 <= m["prep"]["p90_coverage"] <= 0.97  # small training sample in tests
    assert m["travel"]["mae_min"] <= m["travel"]["prior_mae_min"] * 1.05
    assert m["acceptance"]["auc"] > 0.75
    assert m["acceptance"]["coef_reward"] > 0 > m["acceptance"]["coef_extra_min"]
    assert m["coordination"]["auc"] > 0.6


def test_registry_roundtrip_and_fallback(trained, tmp_path) -> None:  # noqa: ANN001
    root, _ = trained
    b = load_bundle(root / "models", TravelParams())
    assert b.describe()["prep"].startswith("hgb") and b.describe()["coordination"].startswith("logreg")
    mean, p90 = b.prep.predict([PrepFeatures(True, 12, 2, 20, False, 3)])
    assert p90[0] >= mean[0] >= 1.0
    # corrupted artefact -> analytic prior, never a crash
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ARTEFACTS["prep"]).write_bytes(b"not a pickle")
    (bad / "metadata.json").write_text("{broken")
    fb = load_bundle(bad, TravelParams())
    assert fb.describe()["prep"] == "analytic-prep-v1"
    assert load_bundle(tmp_path / "missing", TravelParams()).describe()["travel"] == "analytic-travel-v1"


def test_learned_quote_still_valid(cfg, oracle, make_order, trained) -> None:  # noqa: ANN001
    root, _ = trained
    eio = IncentiveOptimizer(cfg, load_bundle(root / "models", TravelParams()))
    offers = eio.quote(
        make_order(ready=10, promised=30), CustomerContext(0.7, True), DensityContext(9, 9, 9), oracle, 20
    )
    for off in offers:
        assert off.reward <= cfg.flex.max_reward_share * off.expected_savings + 1e-9


def test_training_errors(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(FileNotFoundError):
        train_all(tmp_path, tmp_path / "m")
    GeoPoint(0, 0)


def test_version_mismatch_uses_priors(trained, tmp_path) -> None:  # noqa: ANN001
    import json
    import shutil

    root, _ = trained
    copy = tmp_path / "old-models"
    shutil.copytree(root / "models", copy)
    meta = json.loads((copy / "metadata.json").read_text())
    meta["sklearn_version"] = "0.0.1"
    (copy / "metadata.json").write_text(json.dumps(meta))
    b = load_bundle(copy, TravelParams())
    assert b.describe() == analytic_bundle(TravelParams()).describe()
    assert b.metadata["skipped_reason"] == "sklearn_version_mismatch"


def test_failing_artefact_rejected_by_smoke_test(trained, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import shutil

    from flexdrop.ml.models import LearnedCoordination

    root, _ = trained
    copy = tmp_path / "models"
    shutil.copytree(root / "models", copy)

    def broken(self, rows):  # noqa: ANN001, ANN202
        raise AttributeError("'LogisticRegression' object has no attribute 'multi_class'")

    monkeypatch.setattr(LearnedCoordination, "predict_proba", broken)
    b = load_bundle(copy, TravelParams())
    assert b.describe()["coordination"] == "analytic-coordination-v1"
    assert b.describe()["prep"].startswith("hgb")

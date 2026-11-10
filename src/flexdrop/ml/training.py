"""Model training pipeline.

Reads the historical CSVs produced by :mod:`flexdrop.simulation.history` (and optionally the
coordination samples logged by simulation runs), fits the learned models, evaluates them on
a held-out split against the analytic priors, and persists artefacts + metrics.
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, roc_auc_score

from flexdrop.clock import hour_cyclic
from flexdrop.config import TravelParams
from flexdrop.domain.models import Category
from flexdrop.ml.features import (
    ACCEPT_FEATURES,
    COORD_FEATURES,
    PREP_FEATURES,
    TRAVEL_FEATURES,
    AcceptanceFeatures,
    CoordinationFeatures,
    PrepFeatures,
    acceptance_matrix,
    coordination_matrix,
    prep_matrix,
)
from flexdrop.ml.models import (
    AnalyticAcceptance,
    AnalyticCoordination,
    AnalyticPrepTime,
    AnalyticTravelTime,
    LearnedAcceptance,
    LearnedCoordination,
    LearnedPrepTime,
    LearnedTravelTime,
)
from flexdrop.ml.registry import ARTEFACTS, save_model, write_metadata

log = logging.getLogger(__name__)

MIN_COORDINATION_SAMPLES = 200


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"training file not found: {path}")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"training file is empty: {path}")
    return rows


def _split(n: int, seed: int, test_share: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    idx = np.random.default_rng(seed).permutation(n)
    cut = int(n * (1 - test_share))
    return idx[:cut], idx[cut:]


def _hour_from_row(r: dict[str, str]) -> float:
    return float(r["hour"])


def train_prep(rows: list[dict[str, str]], seed: int) -> tuple[LearnedPrepTime, dict[str, float]]:
    feats = [
        PrepFeatures(
            is_food=r["category"] == Category.FOOD.value,
            store_base_prep=float(r["store_base_prep"]),
            item_count=int(r["item_count"]),
            hour=_hour_from_row(r),
            is_weekend=r["is_weekend"] == "1",
            store_load=int(r["store_load"]),
        )
        for r in rows
    ]
    x = prep_matrix(feats)
    y = np.array([float(r["prep_minutes"]) for r in rows])
    tr, te = _split(len(y), seed)
    model = LearnedPrepTime.fit(x[tr], y[tr], seed)
    mean, p90 = model.predict([feats[i] for i in te])
    prior_mean, _ = AnalyticPrepTime().predict([feats[i] for i in te])
    return model, {
        "rows": float(len(y)),
        "mae_min": float(mean_absolute_error(y[te], mean)),
        "prior_mae_min": float(mean_absolute_error(y[te], prior_mean)),
        "p90_coverage": float(np.mean(y[te] <= p90)),
    }


def train_travel(
    rows: list[dict[str, str]], tz_offset_min: int, travel: TravelParams, seed: int
) -> tuple[LearnedTravelTime, dict[str, float]]:
    km = np.array([float(r["road_km"]) for r in rows])
    hours = np.array([float(r["hour"]) for r in rows])
    weekend = np.array([float(r["is_weekend"]) for r in rows])
    cyc = np.array([hour_cyclic(h) for h in hours])
    x = np.column_stack([km, cyc[:, 0], cyc[:, 1], weekend])
    y = np.array([float(r["minutes"]) for r in rows])
    tr, te = _split(len(y), seed)
    model = LearnedTravelTime.fit(x[tr], y[tr], tz_offset_min, seed)
    pred = np.maximum(model.model.predict(x[te]), 0.5)
    prior = AnalyticTravelTime(travel)
    # evaluate the prior at each row's own hour (prior is defined on clock time)
    prior_pred = np.array(
        [
            prior.predict_minutes(np.array([k]), (h * 60.0) - tz_offset_min)[0]
            for k, h in zip(km[te], hours[te], strict=True)
        ]
    )
    return model, {
        "rows": float(len(y)),
        "mae_min": float(mean_absolute_error(y[te], pred)),
        "prior_mae_min": float(mean_absolute_error(y[te], prior_pred)),
    }


def train_acceptance(rows: list[dict[str, str]], seed: int) -> tuple[LearnedAcceptance, dict[str, float]]:
    feats = [
        AcceptanceFeatures(
            reward=float(r["reward"]),
            extra_min=float(r["extra_min"]),
            past_rate=float(r["past_rate"]),
            is_student=r["is_student"] == "1",
            basket_value=float(r["basket_value"]),
            hour=float(r["hour"]),
        )
        for r in rows
    ]
    x = acceptance_matrix(feats)
    y = np.array([int(r["accepted"]) for r in rows])
    if len(set(y.tolist())) < 2:
        raise ValueError("acceptance data needs both accepted and declined offers")
    tr, te = _split(len(y), seed)
    model = LearnedAcceptance.fit(x[tr], y[tr])
    p = model.predict_proba([feats[i] for i in te])
    prior = AnalyticAcceptance().predict_proba([feats[i] for i in te])
    coef = model.coefficients()
    return model, {
        "rows": float(len(y)),
        "auc": float(roc_auc_score(y[te], p)),
        "prior_auc": float(roc_auc_score(y[te], prior)),
        "log_loss": float(log_loss(y[te], p)),
        "brier": float(brier_score_loss(y[te], p)),
        "coef_reward": float(coef[ACCEPT_FEATURES.index("reward")]),
        "coef_extra_min": float(coef[ACCEPT_FEATURES.index("extra_min")]),
        "base_rate": float(y.mean()),
    }


def train_coordination(rows: list[dict[str, str]], seed: int) -> tuple[LearnedCoordination, dict[str, float]]:
    feats = [
        CoordinationFeatures(
            extra_min=float(r["extra_min"]),
            n_near_pickup=int(r["n_near_pickup"]),
            n_near_drop=int(r["n_near_drop"]),
            n_near_both=int(r["n_near_both"]),
            hour=float(r["hour"]),
            is_food=r["is_food"] == "1",
        )
        for r in rows
    ]
    x = coordination_matrix(feats)
    y = np.array([int(r["coordinated"]) for r in rows])
    if len(set(y.tolist())) < 2:
        raise ValueError("coordination data needs both coordinated and solo orders")
    tr, te = _split(len(y), seed)
    model = LearnedCoordination.fit(x[tr], y[tr])
    p = model.predict_proba([feats[i] for i in te])
    prior = AnalyticCoordination().predict_proba([feats[i] for i in te])
    return model, {
        "rows": float(len(y)),
        "auc": float(roc_auc_score(y[te], p)),
        "prior_auc": float(roc_auc_score(y[te], prior)),
        "brier": float(brier_score_loss(y[te], p)),
        "base_rate": float(y.mean()),
    }


def write_coordination_samples(samples: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["extra_min", "n_near_pickup", "n_near_drop", "n_near_both", "hour", "is_food", "coordinated"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in samples:
            w.writerow({c: s[c] for c in cols})


def train_all(
    data_dir: str | Path,
    model_dir: str | Path,
    travel: TravelParams | None = None,
    seed: int = 7,
) -> dict[str, Any]:
    """Train every model whose data is present; return the metrics written to metadata."""
    travel = travel or TravelParams()
    data = Path(data_dir)
    out = Path(model_dir)
    metrics: dict[str, Any] = {}

    prep, metrics["prep"] = train_prep(_read_csv(data / "historical_prep.csv"), seed)
    save_model(out, "prep", prep)
    trav, metrics["travel"] = train_travel(_read_csv(data / "historical_trips.csv"), travel.tz_offset_min, travel, seed)
    save_model(out, "travel", trav)
    acc, metrics["acceptance"] = train_acceptance(_read_csv(data / "offer_history.csv"), seed)
    save_model(out, "acceptance", acc)

    coord_path = data / "coordination_samples.csv"
    if coord_path.exists():
        rows = _read_csv(coord_path)
        if len(rows) >= MIN_COORDINATION_SAMPLES:
            coord, metrics["coordination"] = train_coordination(rows, seed)
            save_model(out, "coordination", coord)
        else:
            metrics["coordination"] = {"skipped": f"only {len(rows)} samples (< {MIN_COORDINATION_SAMPLES})"}
    else:
        stale = out / ARTEFACTS["coordination"]
        if stale.exists():
            stale.unlink()
        metrics["coordination"] = {"skipped": "no coordination_samples.csv; analytic prior in use"}

    write_metadata(
        out,
        {
            "trained_at": datetime.now(tz=UTC).isoformat(),
            "seed": seed,
            "features": {
                "prep": PREP_FEATURES,
                "travel": TRAVEL_FEATURES,
                "acceptance": ACCEPT_FEATURES,
                "coordination": COORD_FEATURES,
            },
            "metrics": metrics,
        },
    )
    log.info("training complete: %s", metrics)
    return metrics

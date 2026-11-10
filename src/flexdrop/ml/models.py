"""Prediction models.

Each predictor has two implementations:

* a *learned* model (scikit-learn) trained by :mod:`flexdrop.ml.training`, and
* an *analytic prior* used when no trained artefact is available (cold start). The priors
  are explicit, documented formulas (docs/algorithms.md section 6), not stubs.

Model choice:
* Preparation time - HistGradientBoosting (squared error for the mean, quantile loss for P90).
* Travel time - HistGradientBoosting on road distance and time-of-day.
* Offer acceptance - logistic regression (monotone in reward and extra wait by construction
  of a linear logit, which is a product requirement: a higher reward must never lower the
  predicted acceptance).
* Coordination probability - logistic regression on local order density and flex minutes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from flexdrop.clock import is_weekend, local_hour
from flexdrop.config import TravelParams
from flexdrop.ml.features import (
    AcceptanceFeatures,
    CoordinationFeatures,
    PrepFeatures,
    acceptance_matrix,
    coordination_matrix,
    prep_matrix,
    travel_matrix,
)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


def congestion_factor(hour: float) -> float:
    """Speed multiplier by local hour (analytic prior; peaks are slower)."""
    if 8 <= hour < 10.5:
        return 0.8
    if 12 <= hour < 14:
        return 0.9
    if 17.5 <= hour < 21.5:
        return 0.78
    if hour >= 23 or hour < 6:
        return 1.2
    return 1.0


# --------------------------------------------------------------------------- protocols
class PrepTimeModel(Protocol):
    name: str

    def predict(self, rows: Sequence[PrepFeatures]) -> tuple[np.ndarray, np.ndarray]:
        """Return (mean minutes, P90 minutes)."""
        ...


class TravelTimeModel(Protocol):
    name: str

    def predict_minutes(self, road_km: np.ndarray, at_min: float) -> np.ndarray: ...


class AcceptanceModel(Protocol):
    name: str

    def predict_proba(self, rows: Sequence[AcceptanceFeatures]) -> np.ndarray: ...


class CoordinationModel(Protocol):
    name: str

    def predict_proba(self, rows: Sequence[CoordinationFeatures]) -> np.ndarray: ...


# --------------------------------------------------------------------------- analytic priors
class AnalyticPrepTime:
    name = "analytic-prep-v1"

    def predict(self, rows: Sequence[PrepFeatures]) -> tuple[np.ndarray, np.ndarray]:
        mean = np.empty(len(rows))
        for i, r in enumerate(rows):
            if r.is_food:
                mean[i] = r.store_base_prep + 0.5 * max(0, r.item_count - 1) + 0.4 * r.store_load
            else:
                mean[i] = 3.0 + 0.35 * r.item_count + 0.3 * r.store_load
        return mean, mean * 1.3 + 2.0


class AnalyticTravelTime:
    name = "analytic-travel-v1"

    def __init__(self, params: TravelParams) -> None:
        self.p = params

    def predict_minutes(self, road_km: np.ndarray, at_min: float) -> np.ndarray:
        hour = local_hour(at_min, self.p.tz_offset_min)
        speed = self.p.base_speed_kmph * congestion_factor(hour)
        km = np.asarray(road_km, dtype=float)
        return np.where(km < 0.01, 0.0, self.p.leg_overhead_min + km / speed * 60.0)


class AnalyticAcceptance:
    """logit = -1.5 + 0.2*reward - 0.15*extra_min + 2.0*(past_rate - 0.5) + 0.3*is_student."""

    name = "analytic-acceptance-v1"

    def predict_proba(self, rows: Sequence[AcceptanceFeatures]) -> np.ndarray:
        x = np.array(
            [-1.5 + 0.2 * r.reward - 0.15 * r.extra_min + 2.0 * (r.past_rate - 0.5) + 0.3 * r.is_student for r in rows]
        )
        return sigmoid(x)


class AnalyticCoordination:
    """q = 1 - exp(-(0.08*n_both + 0.02*n_pickup + 0.01*n_drop) * (1 + extra_min/10))."""

    name = "analytic-coordination-v1"

    def predict_proba(self, rows: Sequence[CoordinationFeatures]) -> np.ndarray:
        out = np.empty(len(rows))
        for i, r in enumerate(rows):
            rate = 0.08 * r.n_near_both + 0.02 * r.n_near_pickup + 0.01 * r.n_near_drop
            out[i] = 1.0 - math.exp(-rate * (1.0 + r.extra_min / 10.0))
        return np.clip(out, 0.02, 0.95)


# --------------------------------------------------------------------------- learned models
class LearnedPrepTime:
    name = "hgb-prep-v1"

    def __init__(self, mean_model: HistGradientBoostingRegressor, p90_model: HistGradientBoostingRegressor):
        self.mean_model = mean_model
        self.p90_model = p90_model

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray, seed: int = 7) -> LearnedPrepTime:
        mean = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, random_state=seed)
        p90 = HistGradientBoostingRegressor(
            loss="quantile", quantile=0.9, max_iter=300, learning_rate=0.06, random_state=seed
        )
        mean.fit(x, y)
        p90.fit(x, y)
        return cls(mean, p90)

    def predict(self, rows: Sequence[PrepFeatures]) -> tuple[np.ndarray, np.ndarray]:
        if not rows:
            return np.empty(0), np.empty(0)
        x = prep_matrix(rows)
        mean = np.maximum(self.mean_model.predict(x), 1.0)
        p90 = np.maximum(self.p90_model.predict(x), mean)
        return mean, p90


class LearnedTravelTime:
    name = "hgb-travel-v1"

    def __init__(self, model: HistGradientBoostingRegressor, tz_offset_min: int):
        self.model = model
        self.tz_offset_min = tz_offset_min

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray, tz_offset_min: int, seed: int = 7) -> LearnedTravelTime:
        model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, random_state=seed)
        model.fit(x, y)
        return cls(model, tz_offset_min)

    def predict_minutes(self, road_km: np.ndarray, at_min: float) -> np.ndarray:
        km = np.asarray(road_km, dtype=float)
        if km.size == 0:
            return np.empty(0)
        x = travel_matrix(km, local_hour(at_min, self.tz_offset_min), is_weekend(at_min, self.tz_offset_min))
        pred = np.maximum(self.model.predict(x), 0.5)
        return np.where(km < 0.01, 0.0, pred)


class LearnedAcceptance:
    name = "logreg-acceptance-v1"

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray) -> LearnedAcceptance:
        pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        pipe.fit(x, y)
        return cls(pipe)

    def coefficients(self) -> np.ndarray:
        """Coefficients in *original* feature units (scaler folded in)."""
        scaler: StandardScaler = self.pipeline.named_steps["standardscaler"]
        lr: LogisticRegression = self.pipeline.named_steps["logisticregression"]
        return lr.coef_[0] / scaler.scale_

    def predict_proba(self, rows: Sequence[AcceptanceFeatures]) -> np.ndarray:
        if not rows:
            return np.empty(0)
        return self.pipeline.predict_proba(acceptance_matrix(rows))[:, 1]


class LearnedCoordination:
    name = "logreg-coordination-v1"

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray) -> LearnedCoordination:
        pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        pipe.fit(x, y)
        return cls(pipe)

    def predict_proba(self, rows: Sequence[CoordinationFeatures]) -> np.ndarray:
        if not rows:
            return np.empty(0)
        return np.clip(self.pipeline.predict_proba(coordination_matrix(rows))[:, 1], 0.02, 0.95)

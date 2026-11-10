"""Feature definitions shared by training, serving and simulation.

Keeping feature construction in one module guarantees train/serve consistency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from flexdrop.clock import hour_cyclic

PREP_FEATURES = ["is_food", "store_base_prep", "item_count", "hour_sin", "hour_cos", "is_weekend", "store_load"]
TRAVEL_FEATURES = ["road_km", "hour_sin", "hour_cos", "is_weekend"]
ACCEPT_FEATURES = ["reward", "extra_min", "past_rate", "is_student", "basket_value", "hour_sin", "hour_cos"]
COORD_FEATURES = ["extra_min", "n_near_pickup", "n_near_drop", "n_near_both", "hour_sin", "hour_cos", "is_food"]


@dataclass(frozen=True, slots=True)
class PrepFeatures:
    is_food: bool
    store_base_prep: float
    item_count: int
    hour: float
    is_weekend: bool
    store_load: int


@dataclass(frozen=True, slots=True)
class AcceptanceFeatures:
    reward: float
    extra_min: float
    past_rate: float  # customer's historical flexible-offer acceptance rate (0..1)
    is_student: bool
    basket_value: float
    hour: float


@dataclass(frozen=True, slots=True)
class CoordinationFeatures:
    extra_min: float
    n_near_pickup: int
    n_near_drop: int
    n_near_both: int
    hour: float
    is_food: bool


def prep_matrix(rows: Sequence[PrepFeatures]) -> np.ndarray:
    out = np.empty((len(rows), len(PREP_FEATURES)), dtype=float)
    for i, r in enumerate(rows):
        s, c = hour_cyclic(r.hour)
        out[i] = (float(r.is_food), r.store_base_prep, r.item_count, s, c, float(r.is_weekend), r.store_load)
    return out


def travel_matrix(road_km: np.ndarray, hour: float, weekend: bool) -> np.ndarray:
    s, c = hour_cyclic(hour)
    n = len(road_km)
    return np.column_stack([road_km, np.full(n, s), np.full(n, c), np.full(n, float(weekend))])


def acceptance_matrix(rows: Sequence[AcceptanceFeatures]) -> np.ndarray:
    out = np.empty((len(rows), len(ACCEPT_FEATURES)), dtype=float)
    for i, r in enumerate(rows):
        s, c = hour_cyclic(r.hour)
        out[i] = (r.reward, r.extra_min, r.past_rate, float(r.is_student), r.basket_value, s, c)
    return out


def coordination_matrix(rows: Sequence[CoordinationFeatures]) -> np.ndarray:
    out = np.empty((len(rows), len(COORD_FEATURES)), dtype=float)
    for i, r in enumerate(rows):
        s, c = hour_cyclic(r.hour)
        out[i] = (r.extra_min, r.n_near_pickup, r.n_near_drop, r.n_near_both, s, c, float(r.is_food))
    return out

"""Per-decision-epoch travel oracle.

Route enumeration queries thousands of point pairs. The oracle batches the ML prediction
for all relevant points once per epoch (one vectorised call) and memoises results.
Time-of-day is frozen at the epoch time, which is accurate for routes shorter than ~90 min.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from flexdrop.config import TravelParams
from flexdrop.domain.models import GeoPoint
from flexdrop.geo import road_km
from flexdrop.ml.models import TravelTimeModel


class TravelOracle:
    def __init__(self, model: TravelTimeModel, params: TravelParams, at_min: float, scale: float = 1.0):
        self.model = model
        self.params = params
        self.at_min = at_min
        self.scale = scale
        self._minutes: dict[tuple[GeoPoint, GeoPoint], float] = {}
        self._km: dict[tuple[GeoPoint, GeoPoint], float] = {}

    def km(self, a: GeoPoint, b: GeoPoint) -> float:
        key = (a, b)
        v = self._km.get(key)
        if v is None:
            v = road_km(a, b, self.params.road_factor)
            self._km[key] = v
            self._km[(b, a)] = v
        return v

    def minutes(self, a: GeoPoint, b: GeoPoint) -> float:
        key = (a, b)
        v = self._minutes.get(key)
        if v is None:
            v = float(self.model.predict_minutes(np.array([self.km(a, b)]), self.at_min)[0]) * self.scale
            self._minutes[key] = v
        return v

    def minutes_for_km(self, distance_km: float) -> float:
        """Predicted minutes for a leg of the given road distance at the epoch time."""
        return float(self.model.predict_minutes(np.array([distance_km]), self.at_min)[0]) * self.scale

    def prime(self, points: Iterable[GeoPoint]) -> None:
        """Pre-compute all ordered pairs among ``points`` in one batched prediction."""
        pts = list(dict.fromkeys(points))
        pairs = [(a, b) for a in pts for b in pts if (a, b) not in self._minutes]
        if not pairs:
            return
        km = np.array([self.km(a, b) for a, b in pairs])
        mins = self.model.predict_minutes(km, self.at_min) * self.scale
        for (a, b), m in zip(pairs, mins, strict=True):
            self._minutes[(a, b)] = float(m)

    def scaled(self, factor: float) -> TravelOracle:
        """A pessimistic/optimistic view sharing the distance cache."""
        other = TravelOracle(self.model, self.params, self.at_min, self.scale * factor)
        other._km = self._km
        other._minutes = {k: v * factor for k, v in self._minutes.items()}
        return other

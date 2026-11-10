"""Order Compatibility Intelligence (OCI).

Two stages (docs/algorithms.md section 2):

1. **Hard feasibility rules** - handling (temperature segregation, contamination, leaks,
   fragility), vehicle capacity, category policy, spatial and temporal proximity. Any
   violated rule rejects the combination regardless of its financial attractiveness.
2. **Compatibility score** in [0, 1] for feasible pairs, used only to rank candidate
   neighbours during group generation (the optimiser makes the final decision).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from flexdrop.config import EngineConfig
from flexdrop.domain.models import Category, GeoPoint, Order, Vehicle
from flexdrop.engines.travel import TravelOracle
from flexdrop.geo import direction_cosine, haversine_km


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    feasible: bool
    reasons: tuple[str, ...]
    score: float = 0.0


def handling_violations(orders: Sequence[Order], vehicle: Vehicle, cfg: EngineConfig) -> list[str]:
    """Group-level handling and capacity rules. Empty list means compatible."""
    reasons: list[str] = []
    categories = {o.category for o in orders}
    if len(categories) > 1 and not cfg.compat.cross_category_enabled:
        reasons.append("cross_category_disabled")

    any_hot = any(o.handling.hot for o in orders)
    any_cold = any(o.handling.cold for o in orders)
    zones_needed = int(any_hot) + int(any_cold)
    if any_hot and any_cold and (vehicle.compartments < 2 or not vehicle.insulated):
        reasons.append("thermal_segregation_unavailable")
    elif zones_needed > max(1, vehicle.compartments):
        reasons.append("insufficient_compartments")

    for a, b in combinations(orders, 2):
        for x, y in ((a, b), (b, a)):
            if x.handling.raw_meat and y.category == Category.FOOD and not (x.handling.sealed and y.handling.sealed):
                reasons.append("raw_meat_contamination")
            if x.handling.leak_risk and not x.handling.sealed and y.category == Category.FOOD:
                reasons.append("leak_risk")
            if x.handling.fragile and y.handling.weight_kg > cfg.compat.fragile_heavy_kg:
                reasons.append("fragile_crush_risk")

    if sum(o.handling.volume_l for o in orders) > vehicle.volume_l + 1e-9:
        reasons.append("volume_capacity")
    if sum(o.handling.weight_kg for o in orders) > vehicle.weight_kg + 1e-9:
        reasons.append("weight_capacity")
    return sorted(set(reasons))


class CompatibilityEngine:
    def __init__(self, cfg: EngineConfig, travel: TravelOracle):
        self.cfg = cfg
        self.travel = travel
        self._pair_cache: dict[tuple[str, str, Vehicle], CompatibilityResult] = {}

    # -- distances -----------------------------------------------------------------
    def _path_km(self, points: Sequence[GeoPoint]) -> float:
        return sum(self.travel.km(points[i], points[i + 1]) for i in range(len(points) - 1))

    def best_pair_distance(self, a: Order, b: Order) -> float:
        """Shortest precedence-feasible path visiting both pickups and drops."""
        pa, pb, da, db = a.pickup, b.pickup, a.drop, b.drop
        sequences = (
            (pa, pb, da, db),
            (pa, pb, db, da),
            (pb, pa, da, db),
            (pb, pa, db, da),
            (pa, da, pb, db),
            (pb, db, pa, da),
        )
        return min(self._path_km(s) for s in sequences)

    # -- pair evaluation -------------------------------------------------------------
    def pair(self, a: Order, b: Order, vehicle: Vehicle) -> CompatibilityResult:
        key = (a.id, b.id, vehicle) if a.id < b.id else (b.id, a.id, vehicle)
        cached = self._pair_cache.get(key)
        if cached is not None:
            return cached
        result = self._evaluate_pair(a, b, vehicle)
        self._pair_cache[key] = result
        return result

    def _evaluate_pair(self, a: Order, b: Order, vehicle: Vehicle) -> CompatibilityResult:
        c = self.cfg.compat
        rf = self.cfg.travel.road_factor
        reasons = handling_violations((a, b), vehicle, self.cfg)

        pickup_km = haversine_km(a.pickup, b.pickup) * rf
        drop_km = haversine_km(a.drop, b.drop) * rf
        if c.same_store_only and pickup_km > c.same_store_radius_km:
            reasons.append("not_same_store")
        if pickup_km > c.max_pickup_distance_km:
            reasons.append("pickup_too_far")
        if drop_km > c.max_drop_distance_km:
            reasons.append("drop_too_far")
        ready_gap = abs(a.predicted_ready_at - b.predicted_ready_at)
        if ready_gap > c.max_ready_gap_min:
            reasons.append("ready_gap_too_large")

        da = self.travel.km(a.pickup, a.drop)
        db = self.travel.km(b.pickup, b.drop)
        pair_km = self.best_pair_distance(a, b)
        if pair_km > c.max_detour_ratio * max(da, db, 1.0):
            reasons.append("detour_too_large")

        if reasons:
            return CompatibilityResult(False, tuple(sorted(set(reasons))), 0.0)

        approach = self.cfg.travel.approach_estimate_km
        saving_fraction = (approach + da + db - pair_km) / (2 * approach + da + db)
        gap_score = 1.0 - ready_gap / max(c.max_ready_gap_min, 1e-9)
        cosine = direction_cosine(a.pickup, a.drop, b.pickup, b.drop)
        score = 0.6 * max(0.0, saving_fraction) + 0.25 * gap_score + 0.15 * (cosine + 1) / 2
        return CompatibilityResult(True, (), round(min(1.0, max(0.0, score)), 6))

    def group(self, orders: Sequence[Order], vehicle: Vehicle) -> CompatibilityResult:
        """A group is compatible iff every pair is and group-level handling/capacity holds."""
        reasons: list[str] = []
        scores: list[float] = []
        for a, b in combinations(orders, 2):
            r = self.pair(a, b, vehicle)
            if not r.feasible:
                reasons.extend(r.reasons)
            scores.append(r.score)
        reasons.extend(handling_violations(orders, vehicle, self.cfg))
        if reasons:
            return CompatibilityResult(False, tuple(sorted(set(reasons))), 0.0)
        return CompatibilityResult(True, (), min(scores) if scores else 1.0)

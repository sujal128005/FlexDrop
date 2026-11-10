"""Standard delivery promise and local order-density features (shared by API and simulator)."""

from __future__ import annotations

from collections.abc import Iterable

from flexdrop.config import EngineConfig
from flexdrop.domain.models import GeoPoint
from flexdrop.engines.incentive import DensityContext
from flexdrop.geo import haversine_km


def standard_promise(
    placed_at: float,
    predicted_prep_min: float,
    trip_minutes: float,
    approach_minutes: float,
    cfg: EngineConfig,
) -> float:
    """Promise shown for standard delivery::

    promised = max(placed + prep_hat, placed + approach_hat) + service_pickup + trip_hat + buffer
    """
    ready = placed_at + predicted_prep_min
    pickup_at = max(ready, placed_at + approach_minutes)
    return pickup_at + cfg.travel.pickup_service_min + trip_minutes + cfg.promise.eta_buffer_min


def compute_density(
    pickup: GeoPoint,
    drop: GeoPoint,
    open_orders: Iterable[tuple[GeoPoint, GeoPoint]],
    radius_km: float,
) -> DensityContext:
    """Count open orders whose pickup / drop / both lie within ``radius_km``."""
    near_p = near_d = near_both = 0
    for p, d in open_orders:
        close_p = haversine_km(pickup, p) <= radius_km
        close_d = haversine_km(drop, d) <= radius_km
        near_p += close_p
        near_d += close_d
        near_both += close_p and close_d
    return DensityContext(n_near_pickup=near_p, n_near_drop=near_d, n_near_both=near_both)

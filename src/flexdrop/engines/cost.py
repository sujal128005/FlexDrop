"""Fulfilment cost model.

Route cost (docs/math_model.md section 2)::

    pay        = F_base + c_km * D + c_min * T + F_drop * (n - 1)
    top_up     = max(0, E_min * T / 60 - pay)            # partner earnings floor
    C_route    = pay + top_up + c_coord * (n - 1) + F_overhead

with D the road distance including the approach leg, T the engaged minutes (approach,
waiting, service and travel) and n the number of orders on the route.
"""

from __future__ import annotations

from flexdrop.config import CostParams, TravelParams
from flexdrop.domain.models import CostBreakdown, GeoPoint
from flexdrop.geo import road_km


class CostModel:
    def __init__(self, params: CostParams):
        self.p = params

    def route_cost(self, distance_km: float, engaged_min: float, n_orders: int) -> CostBreakdown:
        if distance_km < 0 or engaged_min < 0 or n_orders < 0:
            raise ValueError("cost inputs must be non-negative")
        extra = max(0, n_orders - 1)
        base = self.p.base_route_fee
        dist = self.p.per_km * distance_km
        time = self.p.per_engaged_minute * engaged_min
        drops = self.p.extra_drop_fee * extra
        pay = base + dist + time + drops
        floor = self.p.min_partner_hourly_earnings * engaged_min / 60.0
        top_up = max(0.0, floor - pay)
        return CostBreakdown(
            base=base,
            distance=dist,
            time=time,
            extra_drops=drops,
            earnings_top_up=top_up,
            coordination=self.p.coordination_overhead_per_extra_order * extra,
            overhead=self.p.platform_overhead_per_route,
        )

    def solo_baseline(
        self,
        pickup: GeoPoint,
        drop: GeoPoint,
        trip_minutes: float,
        approach_minutes: float,
        travel: TravelParams,
        expected_wait_min: float = 0.0,
    ) -> float:
        """Expected cost of serving an order alone with a typical partner approach."""
        km = travel.approach_estimate_km + road_km(pickup, drop, travel.road_factor)
        minutes = (
            approach_minutes + expected_wait_min + travel.pickup_service_min + trip_minutes + travel.drop_service_min
        )
        return self.route_cost(km, minutes, 1).total

    def marginal_order_cost(self, extra_km: float, extra_minutes: float) -> float:
        """Approximate cost of adding one order to an existing group."""
        return (
            self.p.extra_drop_fee
            + self.p.coordination_overhead_per_extra_order
            + self.p.per_km * extra_km
            + self.p.per_engaged_minute * extra_minutes
        )

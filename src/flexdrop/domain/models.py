"""Domain entities.

Time is represented as ``float`` minutes on a common clock. The API uses minutes since the
Unix epoch (UTC); the simulator uses the same clock so models trained offline transfer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from flexdrop.config import QualityParams


class Category(StrEnum):
    FOOD = "FOOD"
    GROCERY = "GROCERY"  # Instamart


class TempClass(StrEnum):
    HOT = "HOT"
    AMBIENT = "AMBIENT"
    CHILLED = "CHILLED"
    FROZEN = "FROZEN"


class DeliveryMode(StrEnum):
    STANDARD = "STANDARD"
    FLEX = "FLEX"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PLANNED = "PLANNED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PartnerStatus(StrEnum):
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    OFFLINE = "OFFLINE"


class StopType(StrEnum):
    PICKUP = "PICKUP"
    DROP = "DROP"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    lat: float
    lng: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0) or not (-180.0 <= self.lng <= 180.0):
            raise ValueError(f"invalid coordinate ({self.lat}, {self.lng})")


@dataclass(frozen=True, slots=True)
class Handling:
    """Aggregated handling requirements of an order's items."""

    temp_classes: frozenset[TempClass] = frozenset({TempClass.AMBIENT})
    volume_l: float = 5.0
    weight_kg: float = 1.0
    leak_risk: bool = False
    raw_meat: bool = False
    sealed: bool = True
    fragile: bool = False

    def __post_init__(self) -> None:
        if self.volume_l <= 0 or self.weight_kg <= 0:
            raise ValueError("volume and weight must be positive")
        if not self.temp_classes:
            raise ValueError("at least one temperature class is required")

    @property
    def hot(self) -> bool:
        return TempClass.HOT in self.temp_classes

    @property
    def cold(self) -> bool:
        return bool({TempClass.CHILLED, TempClass.FROZEN} & self.temp_classes)


@dataclass(frozen=True, slots=True)
class Vehicle:
    volume_l: float = 45.0
    weight_kg: float = 15.0
    compartments: int = 2  # thermally separated compartments in the delivery bag
    insulated: bool = True


@dataclass(slots=True)
class Store:
    id: str
    name: str
    category: Category
    location: GeoPoint
    base_prep_min: float = 12.0
    zone: str = "default"


@dataclass(slots=True)
class Order:
    id: str
    store_id: str
    category: Category
    pickup: GeoPoint
    drop: GeoPoint
    placed_at: float
    predicted_ready_at: float
    ready_p90_at: float
    promised_at: float  # standard delivery promise
    handling: Handling = field(default_factory=Handling)
    item_count: int = 1
    basket_value: float = 0.0
    mode: DeliveryMode = DeliveryMode.STANDARD
    max_extra_wait: float = 0.0  # w_i: customer-approved extra minutes (0 for standard)
    reward: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    zone: str = "default"

    @property
    def latest_delivery_at(self) -> float:
        """Hard commitment: promise + approved flexibility."""
        return self.promised_at + self.max_extra_wait

    def max_age_min(self, q: QualityParams) -> float:
        """Maximum minutes from (true) readiness to handover for this order's items."""
        limits = {
            TempClass.HOT: q.max_age_hot_min,
            TempClass.CHILLED: q.max_age_chilled_min,
            TempClass.FROZEN: q.max_age_frozen_min,
            TempClass.AMBIENT: q.max_age_ambient_min,
        }
        return min(limits[t] for t in self.handling.temp_classes)

    def planning_age_limit(self, q: QualityParams) -> float:
        """Age limit measured from the *predicted* ready time, conservative by the
        readiness uncertainty (food can be ready earlier than predicted)."""
        return self.max_age_min(q) - q.ready_uncertainty_min


@dataclass(slots=True)
class Partner:
    id: str
    location: GeoPoint
    available_at: float
    vehicle: Vehicle = field(default_factory=Vehicle)
    status: PartnerStatus = PartnerStatus.IDLE
    zone: str = "default"


@dataclass(frozen=True, slots=True)
class Stop:
    order_id: str
    type: StopType
    location: GeoPoint
    arrival: float
    departure: float
    wait: float = 0.0


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    base: float
    distance: float
    time: float
    extra_drops: float
    earnings_top_up: float
    coordination: float
    overhead: float

    @property
    def partner_pay(self) -> float:
        return self.base + self.distance + self.time + self.extra_drops + self.earnings_top_up

    @property
    def total(self) -> float:
        return self.partner_pay + self.coordination + self.overhead


@dataclass(frozen=True, slots=True, eq=False)
class RoutePlan:
    partner_id: str
    order_ids: tuple[str, ...]
    stops: tuple[Stop, ...]
    start_at: float
    start_location: GeoPoint
    distance_km: float
    cost: CostBreakdown
    delivery_at: dict[str, float]
    flex_used: dict[str, float]  # s_i, 0 <= s_i <= w_i
    lateness: dict[str, float]  # minutes beyond the hard commitment (0 when feasible)
    risk_violations: int
    objective: float

    @property
    def end_at(self) -> float:
        return self.stops[-1].departure if self.stops else self.start_at

    @property
    def duration_min(self) -> float:
        return self.end_at - self.start_at

    @property
    def total_wait(self) -> float:
        return sum(s.wait for s in self.stops)

    @property
    def is_late(self) -> bool:
        return any(v > 1e-9 for v in self.lateness.values())


@dataclass(slots=True)
class ActiveRoute:
    """A route already handed to dispatch, used for insertion and re-optimisation."""

    route_id: str
    plan: RoutePlan
    partner_position: GeoPoint
    position_time: float
    changes: int = 0
    started: bool = False  # True once any stop has been served


class ColumnKind(StrEnum):
    NEW = "NEW"
    INSERT = "INSERT"
    FALLBACK = "FALLBACK"


@dataclass(slots=True, eq=False)
class PlannedRoute:
    kind: ColumnKind
    route: RoutePlan
    value: float
    baseline_cost: float  # sum of solo baseline costs of the orders served
    replaces_route_id: str | None = None
    new_order_ids: tuple[str, ...] = ()

    @property
    def expected_savings(self) -> float:
        return self.baseline_cost - self.route.cost.total


@dataclass(slots=True)
class OptimizationStats:
    n_orders: int = 0
    n_partners: int = 0
    n_active_routes: int = 0
    n_groups: int = 0
    n_columns: int = 0
    solver: str = ""
    status: str = ""
    objective: float = 0.0
    latency_ms: float = 0.0


@dataclass(slots=True)
class DispatchDecision:
    routes: list[PlannedRoute] = field(default_factory=list)
    held_order_ids: list[str] = field(default_factory=list)
    deferred_order_ids: list[str] = field(default_factory=list)
    unassigned_due_order_ids: list[str] = field(default_factory=list)
    stats: OptimizationStats = field(default_factory=OptimizationStats)

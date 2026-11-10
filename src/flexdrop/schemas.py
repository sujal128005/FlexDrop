"""Request/response schemas (pydantic v2) - the validated contract of the HTTP API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from flexdrop.domain.models import Category, PartnerStatus, TempClass

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Location(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# ---------------------------------------------------------------- stores
class StoreIn(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=200)
    category: Category
    location: Location
    base_prep_min: float = Field(default=12.0, gt=0, le=120)
    zone: Identifier = "default"


class StoreOut(StoreIn):
    pass


# ---------------------------------------------------------------- partners
class VehicleIn(StrictModel):
    volume_l: float = Field(default=45.0, gt=0, le=500)
    weight_kg: float = Field(default=15.0, gt=0, le=200)
    compartments: int = Field(default=2, ge=1, le=4)
    insulated: bool = True


class PartnerIn(StrictModel):
    id: Identifier
    location: Location
    status: PartnerStatus = PartnerStatus.IDLE
    available_at: AwareDatetime | None = None
    vehicle: VehicleIn = Field(default_factory=VehicleIn)
    zone: Identifier = "default"


class PartnerOut(StrictModel):
    id: str
    location: Location
    status: PartnerStatus
    available_at: datetime
    vehicle: VehicleIn
    zone: str


# ---------------------------------------------------------------- orders & offers
class HandlingIn(StrictModel):
    temp_classes: list[TempClass] = Field(min_length=1, max_length=4)
    volume_l: float = Field(gt=0, le=200)
    weight_kg: float = Field(gt=0, le=100)
    leak_risk: bool = False
    raw_meat: bool = False
    sealed: bool = True
    fragile: bool = False

    @field_validator("temp_classes")
    @classmethod
    def _unique(cls, v: list[TempClass]) -> list[TempClass]:
        return sorted(set(v))


class CustomerSignals(StrictModel):
    past_flex_acceptance_rate: float = Field(default=0.3, ge=0, le=1)
    is_student: bool = False


class OrderIn(StrictModel):
    id: Identifier
    store_id: Identifier
    customer_id: str = Field(min_length=1, max_length=128)
    drop: Location
    placed_at: AwareDatetime | None = None
    item_count: int = Field(default=1, ge=1, le=200)
    basket_value: float = Field(default=0.0, ge=0, le=1_000_000)
    handling: HandlingIn
    customer: CustomerSignals = Field(default_factory=CustomerSignals)
    request_flex_offers: bool = True


class OfferOut(StrictModel):
    offer_id: str
    extra_minutes: int
    reward: float
    flex_eta: datetime
    expires_at: datetime
    acceptance_probability: float
    coordination_probability: float
    expected_net_value: float


class OrderOut(StrictModel):
    id: str
    store_id: str
    category: Category
    status: str
    mode: str
    placed_at: datetime
    predicted_ready_at: datetime
    standard_eta: datetime
    latest_delivery_at: datetime
    max_extra_wait: float
    reward: float
    reward_status: str
    plan_id: str | None
    offers: list[OfferOut] = Field(default_factory=list)
    offers_unavailable_reason: str | None = None


class PreferenceDecision(StrEnum):
    ACCEPT_FLEX = "ACCEPT_FLEX"
    STANDARD = "STANDARD"


class PreferenceIn(StrictModel):
    order_id: Identifier
    decision: PreferenceDecision
    offer_id: str | None = Field(default=None, max_length=36)
    source: str = Field(default="checkout", max_length=64)


class CancelOrderIn(StrictModel):
    reason: str = Field(default="customer_request", min_length=1, max_length=128)
    at: AwareDatetime | None = None


class PreferenceOut(StrictModel):
    order_id: str
    mode: str
    max_extra_wait: float
    reward: float
    standard_eta: datetime
    latest_delivery_at: datetime


# ---------------------------------------------------------------- optimisation & plans
class OptimizeIn(StrictModel):
    zone: Identifier | None = None
    as_of: AwareDatetime | None = None
    dry_run: bool = False


class StopOut(StrictModel):
    seq: int
    order_id: str
    type: str
    location: Location
    eta: datetime
    etd: datetime
    wait_min: float


class PlanOut(StrictModel):
    plan_id: str | None
    version: int
    status: str
    kind: str
    partner_id: str
    order_ids: list[str]
    stops: list[StopOut]
    cost_total: float
    partner_pay: float
    cost_breakdown: dict[str, float]
    distance_km: float
    start_at: datetime
    end_at: datetime
    baseline_cost: float
    expected_savings: float
    flex_allocation: dict[str, float]
    lateness: dict[str, float]
    risk_violations: int


class OptimizeOut(StrictModel):
    run_id: str
    mode: str
    status: str
    as_of: datetime
    dry_run: bool
    latency_ms: float
    plans: list[PlanOut]
    held_order_ids: list[str]
    deferred_order_ids: list[str]
    unassigned_due_order_ids: list[str]


class PlanEventType(StrEnum):
    DISPATCH_ACCEPTED = "DISPATCH_ACCEPTED"
    DISPATCH_REJECTED = "DISPATCH_REJECTED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    PREP_DELAY = "PREP_DELAY"
    PARTNER_UNAVAILABLE = "PARTNER_UNAVAILABLE"


class PlanEventIn(StrictModel):
    type: PlanEventType
    order_id: Identifier | None = None
    at: AwareDatetime | None = None
    new_ready_at: AwareDatetime | None = None
    partner_location: Location | None = None


class PlanEventOut(StrictModel):
    plan: PlanOut
    action: str
    released_order_ids: list[str]
    at_risk_order_ids: list[str]


class KillSwitchIn(StrictModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=256)


class KillSwitchOut(StrictModel):
    enabled: bool
    updated_by: str | None
    updated_at: datetime | None
    reason: str | None


class MetricsOut(BaseModel):
    window_hours: float
    generated_at: datetime
    values: dict[str, Any]

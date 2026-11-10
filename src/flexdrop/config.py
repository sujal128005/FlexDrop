"""Configuration.

Two layers:

* :class:`EngineConfig` - immutable, pure-Python parameters of the decision engines. It is
  used by the API service *and* by the offline simulator, so it has no environment coupling.
* :class:`Settings` - process/runtime settings loaded from environment variables
  (prefix ``FLEXDROP_``) or a ``.env`` file.

Every monetary value is in Indian Rupees (INR) and every duration is in minutes. All cost
figures are *hypothetical modelling assumptions*, not Swiggy's real operating costs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-pseudonymization-secret-change-me"  # noqa: S105 - dev default, rejected in prod


@dataclass(frozen=True)
class CostParams:
    """Fulfilment cost model (see docs/math_model.md section 2)."""

    base_route_fee: float = 22.0  # paid to the partner per dispatched route
    per_km: float = 7.0  # partner pay per road-km (approach + route)
    per_engaged_minute: float = 0.8  # partner pay per engaged minute (incl. waiting)
    extra_drop_fee: float = 12.0  # partner bonus for every drop beyond the first
    platform_overhead_per_route: float = 3.0  # platform cost of dispatching one route
    coordination_overhead_per_extra_order: float = 1.5  # support/ops cost of coordination
    min_partner_hourly_earnings: float = 140.0  # earnings floor per engaged hour (top-up)


@dataclass(frozen=True)
class TravelParams:
    road_factor: float = 1.35  # road distance / great-circle distance
    base_speed_kmph: float = 20.0  # two-wheeler urban speed off-peak
    leg_overhead_min: float = 1.0  # parking / starting per leg
    pickup_service_min: float = 2.0
    drop_service_min: float = 2.5
    approach_estimate_km: float = 2.0  # typical partner-to-store distance used for promises
    tz_offset_min: int = 330  # IST, used for time-of-day features


@dataclass(frozen=True)
class PromiseParams:
    eta_buffer_min: float = 8.0  # buffer added to the standard delivery promise


@dataclass(frozen=True)
class QualityParams:
    """Maximum minutes between 'food ready' and handover, per temperature class."""

    max_age_hot_min: float = 40.0
    max_age_chilled_min: float = 60.0
    max_age_frozen_min: float = 45.0
    max_age_ambient_min: float = 180.0
    ready_uncertainty_min: float = 5.0  # food may be ready this much earlier than predicted


@dataclass(frozen=True)
class FlexParams:
    wait_options_min: tuple[int, ...] = (5, 10, 15)
    reward_min: int = 5
    reward_max: int = 30
    reward_step: int = 1
    max_reward_share: float = 0.6  # reward <= share x expected attributable savings
    min_expected_margin: float = 1.0  # minimum expected net INR per accepted offer
    offer_ttl_min: float = 10.0
    expected_detour_km: float = 0.6  # marginal detour of adding an order to a group
    expected_extra_minutes: float = 4.0  # marginal engaged minutes of adding an order
    quality_margin_min: float = 5.0  # safety margin kept from the food-quality limit
    density_radius_km: float = 1.5  # radius for nearby-order density features
    density_window_min: float = 20.0


@dataclass(frozen=True)
class CompatibilityParams:
    cross_category_enabled: bool = False
    max_pickup_distance_km: float = 2.0
    max_drop_distance_km: float = 2.5
    max_ready_gap_min: float = 12.0
    max_detour_ratio: float = 1.6
    fragile_heavy_kg: float = 6.0
    same_store_only: bool = False  # conventional-batching baseline restriction
    same_store_radius_km: float = 0.3


@dataclass(frozen=True)
class OptimizerParams:
    max_group_size: int = 3
    top_k_neighbors: int = 6
    partners_per_group: int = 4
    partners_per_large_group: int = 3  # candidate partners for groups of >= 3 orders
    max_approach_km: float = 6.0
    max_candidate_groups: int = 2500
    assignment_bonus: float = 250.0  # value of serving an order now (dominates costs)
    flex_minute_penalty: float = 0.5  # lambda: INR per allocated flexibility minute
    risk_penalty: float = 25.0  # INR per order that breaks its window under P90 times
    late_minute_penalty: float = 15.0  # INR per late minute (fallback columns only)
    max_store_wait_min: float = 5.0  # just-in-time dispatch: max predicted wait at a store
    max_route_duration_min: float = 80.0
    risk_travel_factor: float = 1.2
    hold_enabled: bool = True
    hold_safety_min: float = 5.0  # tuned in simulation (docs/simulation.md, sensitivity)
    epoch_min: float = 1.0
    insertion_enabled: bool = False
    max_route_changes: int = 2
    insertion_min_gain: float = 5.0
    solver: str = "cpsat"  # cpsat | greedy | fifo
    cpsat_time_limit_s: float = 2.0
    cpsat_workers: int = 4


@dataclass(frozen=True)
class EngineConfig:
    deployment_level: int = 1
    cost: CostParams = field(default_factory=CostParams)
    travel: TravelParams = field(default_factory=TravelParams)
    promise: PromiseParams = field(default_factory=PromiseParams)
    quality: QualityParams = field(default_factory=QualityParams)
    flex: FlexParams = field(default_factory=FlexParams)
    compat: CompatibilityParams = field(default_factory=CompatibilityParams)
    optimizer: OptimizerParams = field(default_factory=OptimizerParams)

    @staticmethod
    def for_level(level: int, **optimizer_overrides: Any) -> EngineConfig:
        """Build the configuration of a deployment level.

        * Level 1 - same-category coordination (Food+Food, Instamart+Instamart), groups <= 3.
        * Level 2 - controlled cross-category coordination, groups <= 3.
        * Level 3 - multi-order coordination: cross-category, groups <= 4, insertion of new
          orders into not-yet-started routes.
        """
        if level not in (1, 2, 3):
            raise ValueError("deployment level must be 1, 2 or 3")
        base = EngineConfig(deployment_level=level)
        compat = replace(base.compat, cross_category_enabled=level >= 2)
        optimizer = replace(
            base.optimizer,
            max_group_size=4 if level == 3 else 3,
            insertion_enabled=level == 3,
            **optimizer_overrides,
        )
        return replace(base, compat=compat, optimizer=optimizer)


class Settings(BaseSettings):
    """Runtime settings (environment variables prefixed ``FLEXDROP_``)."""

    model_config = SettingsConfigDict(env_prefix="FLEXDROP_", env_file=".env", extra="ignore")

    env: str = Field(default="development", pattern="^(development|test|staging|production)$")
    database_url: str = "sqlite:///./flexdrop.db"
    log_level: str = "INFO"
    log_json: bool = True
    api_keys: str = "{}"  # JSON: {"<sha256 of key>": {"name": "...", "roles": ["checkout"]}}
    pseudonymization_secret: SecretStr = SecretStr(DEFAULT_SECRET)
    model_dir: str = "models"
    deployment_level: int = Field(default=1, ge=1, le=3)
    solver: str = Field(default="cpsat", pattern="^(cpsat|greedy)$")
    cpsat_time_limit_s: float = Field(default=2.0, gt=0, le=30)
    rate_limit_per_minute: int = Field(default=600, ge=1)
    max_request_bytes: int = Field(default=1_000_000, ge=1024)
    prometheus_public: bool = False
    data_retention_days: int = Field(default=90, ge=1)
    kill_switch_default: bool = False
    auto_create_schema: bool = True  # dev/test convenience; production uses `alembic upgrade head`
    expose_docs: bool = True  # OpenAPI UI at /docs (forced off in production)
    reports_dir: str = "reports"  # simulation reports served by /v1/reports/simulation

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid log level")
        return v

    @field_validator("api_keys")
    @classmethod
    def _keys(cls, v: str) -> str:
        data = json.loads(v)
        if not isinstance(data, dict):
            raise ValueError("FLEXDROP_API_KEYS must be a JSON object")
        for digest, meta in data.items():
            if len(digest) != 64 or not isinstance(meta, dict) or not isinstance(meta.get("roles"), list):
                raise ValueError("each API key entry must be '<sha256 hex>': {name, roles: [...]}")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.env == "production":
            if self.pseudonymization_secret.get_secret_value() == DEFAULT_SECRET:
                raise ValueError("FLEXDROP_PSEUDONYMIZATION_SECRET must be set in production")
            if len(self.pseudonymization_secret.get_secret_value()) < 32:
                raise ValueError("FLEXDROP_PSEUDONYMIZATION_SECRET must be >= 32 characters")
            if not json.loads(self.api_keys):
                raise ValueError("FLEXDROP_API_KEYS must define at least one key in production")
        return self

    def parsed_api_keys(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.api_keys)

    def engine_config(self) -> EngineConfig:
        return EngineConfig.for_level(
            self.deployment_level, solver=self.solver, cpsat_time_limit_s=self.cpsat_time_limit_s
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

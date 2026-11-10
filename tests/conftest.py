"""Shared fixtures and factories."""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from itertools import count

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from flexdrop.api.app import create_app
from flexdrop.api.security import hash_key
from flexdrop.config import EngineConfig, Settings, TravelParams
from flexdrop.domain.models import (
    Category,
    DeliveryMode,
    GeoPoint,
    Handling,
    Order,
    Partner,
    TempClass,
    Vehicle,
)
from flexdrop.engines.cost import CostModel
from flexdrop.engines.travel import TravelOracle
from flexdrop.geo import offset
from flexdrop.ml.models import AnalyticTravelTime

CENTER = GeoPoint(12.9352, 77.6245)
# 2026-11-09 14:00 UTC (19:30 IST) expressed in engine minutes
T0 = datetime(2026, 11, 9, 14, 0, tzinfo=UTC).timestamp() / 60.0

KEYS = {
    "checkout": "test-checkout-key",
    "dispatch": "test-dispatch-key",
    "analyst": "test-analyst-key",
    "admin": "test-admin-key",
}


@pytest.fixture
def cfg() -> EngineConfig:
    return EngineConfig.for_level(1, cpsat_workers=1, cpsat_time_limit_s=5.0)


@pytest.fixture
def travel_model() -> AnalyticTravelTime:
    return AnalyticTravelTime(TravelParams())


@pytest.fixture
def oracle(cfg: EngineConfig, travel_model: AnalyticTravelTime) -> TravelOracle:
    return TravelOracle(travel_model, cfg.travel, T0)


@pytest.fixture
def cost(cfg: EngineConfig) -> CostModel:
    return CostModel(cfg.cost)


_ids = count()


@pytest.fixture
def make_order() -> Callable[..., Order]:
    def factory(
        pickup: GeoPoint | None = None,
        drop: GeoPoint | None = None,
        ready: float = 0.0,
        promised: float = 40.0,
        flex: float = 0.0,
        category: Category = Category.FOOD,
        temps: frozenset[TempClass] = frozenset({TempClass.HOT}),
        volume: float = 6.0,
        weight: float = 1.5,
        oid: str | None = None,
        p90_extra: float = 0.0,
        **handling_flags: bool,
    ) -> Order:
        pickup = pickup or CENTER
        drop = drop or offset(CENTER, 1.5, 0.0)
        return Order(
            id=oid or f"o{next(_ids)}",
            store_id="S1",
            category=category,
            pickup=pickup,
            drop=drop,
            placed_at=T0 - 10,
            predicted_ready_at=T0 + ready,
            ready_p90_at=T0 + ready + p90_extra,
            promised_at=T0 + promised,
            handling=Handling(temp_classes=temps, volume_l=volume, weight_kg=weight, **handling_flags),
            mode=DeliveryMode.FLEX if flex > 0 else DeliveryMode.STANDARD,
            max_extra_wait=flex,
            reward=10.0 if flex > 0 else 0.0,
        )

    return factory


@pytest.fixture
def make_partner() -> Callable[..., Partner]:
    def factory(pid: str, location: GeoPoint | None = None, vehicle: Vehicle | None = None) -> Partner:
        return Partner(id=pid, location=location or CENTER, available_at=T0, vehicle=vehicle or Vehicle())

    return factory


def level(cfg: EngineConfig, **optimizer: object) -> EngineConfig:
    return replace(cfg, optimizer=replace(cfg.optimizer, **optimizer))


# ---------------------------------------------------------------- API
def api_settings(tmp_path, **overrides: object) -> Settings:  # noqa: ANN001
    keys = {hash_key(v): {"name": f"{k}-client", "roles": [k]} for k, v in KEYS.items()}
    base: dict[str, object] = {
        "database_url": f"sqlite:///{tmp_path}/api.db",
        "model_dir": str(tmp_path / "no-models"),
        "api_keys": json.dumps(keys),
        "log_json": False,
        "log_level": "WARNING",
        "rate_limit_per_minute": 10_000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:  # noqa: ANN001
    app = create_app(api_settings(tmp_path))
    with TestClient(app) as c:
        yield c


def headers(role: str, **extra: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[role], **extra}

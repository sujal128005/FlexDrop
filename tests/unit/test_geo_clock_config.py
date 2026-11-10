from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import pytest

from flexdrop.clock import from_minutes, hour_cyclic, is_weekend, local_hour, to_minutes
from flexdrop.config import DEFAULT_SECRET, EngineConfig, Settings
from flexdrop.domain.models import GeoPoint, Handling, TempClass
from flexdrop.geo import direction_cosine, displacement_km, haversine_km, interpolate, offset, road_km

BLR = GeoPoint(12.9716, 77.5946)
MAA = GeoPoint(13.0827, 80.2707)


def test_haversine_known_distance_and_symmetry() -> None:
    d = haversine_km(BLR, MAA)
    assert 285 < d < 295
    assert haversine_km(MAA, BLR) == pytest.approx(d)
    assert haversine_km(BLR, BLR) == 0.0
    assert road_km(BLR, MAA, 1.35) == pytest.approx(d * 1.35)


def test_offset_and_displacement_are_consistent() -> None:
    p = offset(BLR, 2.0, -1.0)
    east, north = displacement_km(BLR, p)
    assert north == pytest.approx(2.0, abs=1e-3)
    assert east == pytest.approx(-1.0, abs=1e-3)
    assert haversine_km(BLR, p) == pytest.approx(math.sqrt(5), rel=1e-3)


def test_direction_cosine_and_interpolate() -> None:
    a, b = BLR, offset(BLR, 0, 2)
    c, d = offset(BLR, 1, 0), offset(BLR, 1, 2)
    assert direction_cosine(a, b, c, d) == pytest.approx(1.0, abs=1e-6)
    assert direction_cosine(a, b, d, c) == pytest.approx(-1.0, abs=1e-6)
    assert direction_cosine(a, a, c, d) == 1.0
    mid = interpolate(a, b, 0.5)
    assert haversine_km(a, mid) == pytest.approx(1.0, rel=1e-3)
    assert interpolate(a, b, 2.0) == b


def test_geopoint_and_handling_validation() -> None:
    with pytest.raises(ValueError):
        GeoPoint(91, 0)
    with pytest.raises(ValueError):
        Handling(volume_l=0)
    with pytest.raises(ValueError):
        Handling(temp_classes=frozenset())
    h = Handling(temp_classes=frozenset({TempClass.HOT, TempClass.FROZEN}))
    assert h.hot and h.cold


def test_clock_roundtrip_and_local_time() -> None:
    dt = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)  # Monday
    m = to_minutes(dt)
    assert from_minutes(m) == dt
    assert local_hour(m, 330) == pytest.approx(19.5)
    assert not is_weekend(m, 330)
    assert is_weekend(to_minutes(datetime(2026, 11, 8, 12, 0, tzinfo=UTC)), 330)  # Sunday
    s, c = hour_cyclic(6.0)
    assert s == pytest.approx(1.0) and c == pytest.approx(0.0, abs=1e-9)
    assert to_minutes(datetime(2026, 1, 1)) == to_minutes(datetime(2026, 1, 1, tzinfo=UTC))


def test_deployment_levels() -> None:
    l1, l2, l3 = (EngineConfig.for_level(i) for i in (1, 2, 3))
    assert not l1.compat.cross_category_enabled and l2.compat.cross_category_enabled
    assert l1.optimizer.max_group_size == 3 and l3.optimizer.max_group_size == 4
    assert l3.optimizer.insertion_enabled and not l2.optimizer.insertion_enabled
    assert EngineConfig.for_level(1, solver="greedy").optimizer.solver == "greedy"
    with pytest.raises(ValueError):
        EngineConfig.for_level(4)


def test_settings_validation() -> None:
    s = Settings(log_level="debug")
    assert s.log_level == "DEBUG"
    with pytest.raises(ValueError):
        Settings(log_level="loud")
    with pytest.raises(ValueError):
        Settings(api_keys='{"short": {"roles": []}}')
    with pytest.raises(ValueError):
        Settings(api_keys="[]")
    with pytest.raises(ValueError):
        Settings(env="production", api_keys=json.dumps({"a" * 64: {"name": "x", "roles": ["admin"]}}))
    with pytest.raises(ValueError):
        Settings(env="production", pseudonymization_secret="x" * 40)
    prod = Settings(
        env="production",
        pseudonymization_secret="s" * 40,
        api_keys=json.dumps({"a" * 64: {"name": "x", "roles": ["admin"]}}),
    )
    assert prod.pseudonymization_secret.get_secret_value() != DEFAULT_SECRET
    assert prod.engine_config().deployment_level == 1

"""Clock helpers: conversion between datetimes and engine minutes."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def to_minutes(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp() / 60.0


def from_minutes(minutes: float) -> datetime:
    return datetime.fromtimestamp(minutes * 60.0, tz=UTC)


def now_minutes() -> float:
    return to_minutes(datetime.now(tz=UTC))


def local_hour(t_min: float, tz_offset_min: int) -> float:
    """Fractional local hour of day in [0, 24)."""
    return ((t_min + tz_offset_min) / 60.0) % 24.0


def is_weekend(t_min: float, tz_offset_min: int) -> bool:
    days = math.floor((t_min + tz_offset_min) / 1440.0)
    # 1970-01-01 was a Thursday (weekday index 3, Monday=0)
    return (days + 3) % 7 >= 5


def hour_cyclic(hour: float) -> tuple[float, float]:
    angle = 2 * math.pi * hour / 24.0
    return math.sin(angle), math.cos(angle)

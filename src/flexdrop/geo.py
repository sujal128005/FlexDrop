"""Geospatial helpers (haversine distance, local displacement, interpolation)."""

from __future__ import annotations

import math

from flexdrop.domain.models import GeoPoint

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = 2 * math.pi * EARTH_RADIUS_KM / 360.0  # ~111.195 km, consistent with haversine


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    if a == b:
        return 0.0
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def road_km(a: GeoPoint, b: GeoPoint, road_factor: float) -> float:
    return haversine_km(a, b) * road_factor


def displacement_km(a: GeoPoint, b: GeoPoint) -> tuple[float, float]:
    """Local (east, north) displacement from ``a`` to ``b`` in km (equirectangular)."""
    mean_lat = math.radians((a.lat + b.lat) / 2)
    east = (b.lng - a.lng) * KM_PER_DEG_LAT * math.cos(mean_lat)
    north = (b.lat - a.lat) * KM_PER_DEG_LAT
    return east, north


def direction_cosine(p1: GeoPoint, d1: GeoPoint, p2: GeoPoint, d2: GeoPoint) -> float:
    """Cosine of the angle between trip vectors p1->d1 and p2->d2 (1.0 if either is ~0)."""
    e1, n1 = displacement_km(p1, d1)
    e2, n2 = displacement_km(p2, d2)
    l1, l2 = math.hypot(e1, n1), math.hypot(e2, n2)
    if l1 < 0.05 or l2 < 0.05:
        return 1.0
    return (e1 * e2 + n1 * n2) / (l1 * l2)


def offset(point: GeoPoint, north_km: float, east_km: float) -> GeoPoint:
    lat = point.lat + north_km / KM_PER_DEG_LAT
    lng = point.lng + east_km / (KM_PER_DEG_LAT * math.cos(math.radians(point.lat)))
    return GeoPoint(lat, lng)


def interpolate(a: GeoPoint, b: GeoPoint, fraction: float) -> GeoPoint:
    f = min(1.0, max(0.0, fraction))
    return GeoPoint(a.lat + (b.lat - a.lat) * f, a.lng + (b.lng - a.lng) * f)

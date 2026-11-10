"""Synthetic scenario generation (stores, customers, partners, order stream, ground truth).

All randomness is driven by a seeded ``numpy.random.Generator`` so a scenario is fully
reproducible and *identical across policies* (same orders, preparation times, cancellations).

Two built-in scenarios:

* ``campus`` - a residential university campus: deliveries converge on hostel gates, demand
  peaks in the evening, most customers are price-sensitive students.
* ``city`` - a mixed residential neighbourhood with dispersed drop points (harder to batch).

Ground-truth behaviour (preparation, travel, customer utility) is defined in
:class:`GroundTruth`; the decision engines never read it directly - they only see
predictions from models trained on historical samples drawn from it.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from flexdrop.clock import local_hour, to_minutes
from flexdrop.domain.models import Category, GeoPoint, Handling, Store, TempClass, Vehicle
from flexdrop.geo import offset, road_km


@dataclass(frozen=True)
class GroundTruth:
    speed_kmph: float = 19.0
    leg_overhead_min: float = 1.0
    travel_noise_sigma: float = 0.15
    prep_noise_sigma_food: float = 0.18
    prep_noise_sigma_grocery: float = 0.20
    peak_prep_bump_min: float = 3.0
    # customer utility of a flexible offer (m extra minutes, reward r):
    # u = a0 + a_r*r - a_w*m*(0.5 + urgency) + a_p*price_sensitivity + a_s*is_student
    util_a0: float = -1.8
    util_reward: float = 0.22
    util_wait: float = 0.16
    util_price: float = 1.6
    util_student: float = 0.4
    cancel_prob: float = 0.02
    delay_signal_threshold_min: float = 2.0
    partner_dropout_per_hour: float = 0.01

    def congestion(self, hour: float) -> float:
        if 8 <= hour < 10.5:
            return 0.78
        if 12 <= hour < 14:
            return 0.88
        if 17.5 <= hour < 21.5:
            return 0.74
        if hour >= 23 or hour < 6:
            return 1.15
        return 1.0

    def mean_travel_minutes(self, km: float, hour: float) -> float:
        if km < 0.01:
            return 0.0
        return self.leg_overhead_min + km / (self.speed_kmph * self.congestion(hour)) * 60.0

    def utility(self, reward: float, extra_min: float, urgency: float, price_sens: float, student: bool) -> float:
        return (
            self.util_a0
            + self.util_reward * reward
            - self.util_wait * extra_min * (0.5 + urgency)
            + self.util_price * price_sens
            + self.util_student * float(student)
        )


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    center: GeoPoint
    start_iso: str
    duration_min: float
    n_food_stores: int
    n_grocery_stores: int
    store_ring_km: tuple[float, float]
    drop_mode: str  # campus | city
    n_drop_points: int
    area_radius_km: float
    off_campus_share: float
    n_partners: int
    demand_profile: tuple[tuple[float, float], ...]  # (local hour start, orders per minute)
    grocery_share: float
    student_share: float
    n_customers: int
    seed: int = 42
    truth: GroundTruth = field(default_factory=GroundTruth)

    @property
    def start_at(self) -> float:
        from datetime import datetime

        return to_minutes(datetime.fromisoformat(self.start_iso))


def campus_spec(seed: int = 42, duration_min: float = 240.0, demand_scale: float = 1.0) -> ScenarioSpec:
    return ScenarioSpec(
        name="campus",
        center=GeoPoint(12.9352, 77.6245),
        start_iso="2026-11-09T19:00:00+05:30",
        duration_min=duration_min,
        n_food_stores=35,
        n_grocery_stores=2,
        store_ring_km=(0.6, 3.0),
        drop_mode="campus",
        n_drop_points=10,
        area_radius_km=0.9,
        off_campus_share=0.12,
        n_partners=70,
        demand_profile=(
            (19.0, 1.3 * demand_scale),
            (20.0, 2.2 * demand_scale),
            (21.0, 2.6 * demand_scale),
            (22.0, 1.7 * demand_scale),
            (23.0, 0.9 * demand_scale),
        ),
        grocery_share=0.2,
        student_share=0.9,
        n_customers=2500,
        seed=seed,
    )


def city_spec(seed: int = 42, duration_min: float = 240.0, demand_scale: float = 1.0) -> ScenarioSpec:
    return ScenarioSpec(
        name="city",
        center=GeoPoint(12.9716, 77.6412),
        start_iso="2026-11-09T19:00:00+05:30",
        duration_min=duration_min,
        n_food_stores=60,
        n_grocery_stores=4,
        store_ring_km=(0.0, 3.5),
        drop_mode="city",
        n_drop_points=400,
        area_radius_km=3.5,
        off_campus_share=0.0,
        n_partners=76,
        demand_profile=(
            (19.0, 1.3 * demand_scale),
            (20.0, 2.0 * demand_scale),
            (21.0, 2.2 * demand_scale),
            (22.0, 1.4 * demand_scale),
            (23.0, 0.8 * demand_scale),
        ),
        grocery_share=0.25,
        student_share=0.15,
        n_customers=4000,
        seed=seed,
    )


SCENARIOS = {"campus": campus_spec, "city": city_spec}


@dataclass(frozen=True)
class Customer:
    id: str
    drop: GeoPoint
    is_student: bool
    price_sensitivity: float
    urgency: float
    past_acceptance_rate: float


@dataclass(frozen=True)
class OrderRequest:
    id: str
    placed_at: float
    customer_id: str
    store_id: str
    category: Category
    item_count: int
    basket_value: float
    handling: Handling
    store_load: int
    true_prep_min: float
    cancel_after_min: float | None


@dataclass(frozen=True)
class PartnerSeed:
    id: str
    location: GeoPoint
    vehicle: Vehicle
    dropout_at: float | None


@dataclass
class Scenario:
    spec: ScenarioSpec
    stores: list[Store]
    drop_points: list[GeoPoint]
    customers: dict[str, Customer]
    partners: list[PartnerSeed]
    orders: list[OrderRequest]

    @property
    def store_by_id(self) -> dict[str, Store]:
        return {s.id: s for s in self.stores}

    @property
    def start_at(self) -> float:
        return self.spec.start_at

    @property
    def end_at(self) -> float:
        return self.spec.start_at + self.spec.duration_min


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _ring_point(rng: np.random.Generator, center: GeoPoint, r_min: float, r_max: float) -> GeoPoint:
    r = math.sqrt(rng.uniform(r_min**2, r_max**2))
    theta = rng.uniform(0, 2 * math.pi)
    return offset(center, r * math.sin(theta), r * math.cos(theta))


def generate_handling(rng: np.random.Generator, category: Category, items: int) -> Handling:
    if category == Category.FOOD:
        u = rng.random()
        temps = (
            frozenset({TempClass.HOT})
            if u < 0.85
            else frozenset({TempClass.AMBIENT})
            if u < 0.95
            else frozenset({TempClass.HOT, TempClass.CHILLED})
        )
        return Handling(
            temp_classes=temps,
            volume_l=round(2.0 + 1.5 * items + rng.uniform(0, 1.5), 2),
            weight_kg=round(0.4 + 0.35 * items + rng.uniform(0, 0.3), 2),
            leak_risk=bool(rng.random() < 0.10),
            raw_meat=False,
            sealed=bool(rng.random() < 0.95),
            fragile=bool(rng.random() < 0.05),
        )
    grocery_temps: set[TempClass] = {TempClass.AMBIENT}
    if rng.random() < 0.40:
        grocery_temps.add(TempClass.CHILLED)
    if rng.random() < 0.15:
        grocery_temps.add(TempClass.FROZEN)
    return Handling(
        temp_classes=frozenset(grocery_temps),
        volume_l=round(3.0 + 1.2 * items + rng.uniform(0, 3.0), 2),
        weight_kg=round(0.8 + 0.6 * items + rng.uniform(0, 1.0), 2),
        leak_risk=bool(rng.random() < 0.10),
        raw_meat=bool(rng.random() < 0.06),
        sealed=bool(rng.random() < 0.80),
        fragile=bool(rng.random() < 0.15),
    )


def true_prep_minutes(
    rng: np.random.Generator,
    truth: GroundTruth,
    category: Category,
    base_prep: float,
    items: int,
    load: int,
    hour: float,
) -> float:
    if category == Category.FOOD:
        mean = base_prep * (1 + 0.04 * (items - 1)) + 0.5 * load
        if 20 <= hour < 22:
            mean += truth.peak_prep_bump_min
        return float(max(2.0, mean * rng.lognormal(0.0, truth.prep_noise_sigma_food)))
    mean = 3.0 + 0.35 * items + 0.3 * load
    return float(max(1.5, mean * rng.lognormal(0.0, truth.prep_noise_sigma_grocery)))


def demand_rate(spec: ScenarioSpec, hour: float) -> float:
    """Orders/minute: the last profile step starting at or before ``hour`` (00-06h wraps to 24-30h)."""
    h = hour if hour >= 6 else hour + 24
    rate = spec.demand_profile[0][1]
    for start, r in spec.demand_profile:
        if h >= start:
            rate = r
    return rate


def build_scenario(spec: ScenarioSpec, tz_offset_min: int = 330) -> Scenario:
    rng = np.random.default_rng(spec.seed)
    truth = spec.truth

    # stores
    stores: list[Store] = []
    for i in range(spec.n_food_stores):
        loc = _ring_point(rng, spec.center, *spec.store_ring_km)
        stores.append(
            Store(f"F{i:03d}", f"Restaurant {i:03d}", Category.FOOD, loc, round(float(rng.uniform(8, 20)), 2))
        )
    for i in range(spec.n_grocery_stores):
        angle = 2 * math.pi * i / max(1, spec.n_grocery_stores)
        r = (spec.store_ring_km[0] + spec.store_ring_km[1]) / 2
        loc = offset(spec.center, r * math.sin(angle), r * math.cos(angle))
        stores.append(Store(f"G{i:03d}", f"Instamart {i:03d}", Category.GROCERY, loc, 4.0))
    food = [s for s in stores if s.category == Category.FOOD]
    grocery = [s for s in stores if s.category == Category.GROCERY]
    popularity = rng.zipf(1.6, size=len(food)).astype(float)
    popularity = np.minimum(popularity, 20.0)

    # drop points
    if spec.drop_mode == "campus":
        gates = [
            offset(
                spec.center,
                spec.area_radius_km * math.sin(2 * math.pi * k / spec.n_drop_points),
                spec.area_radius_km * math.cos(2 * math.pi * k / spec.n_drop_points),
            )
            for k in range(spec.n_drop_points)
        ]
        off_campus = [_ring_point(rng, spec.center, 1.2, 2.5) for _ in range(40)]
        drop_points = gates + off_campus
    else:
        gates = []
        off_campus = []
        drop_points = [_ring_point(rng, spec.center, 0.0, spec.area_radius_km) for _ in range(spec.n_drop_points)]

    # customers
    customers: dict[str, Customer] = {}
    for i in range(spec.n_customers):
        student = bool(rng.random() < spec.student_share)
        if spec.drop_mode == "campus":
            drop = (
                off_campus[int(rng.integers(len(off_campus)))]
                if rng.random() < spec.off_campus_share
                else gates[int(rng.integers(len(gates)))]
            )
        else:
            drop = drop_points[int(rng.integers(len(drop_points)))]
        price = float(rng.beta(3, 2) if student else rng.beta(2, 3))
        urgency = float(rng.beta(2, 3) if student else rng.beta(3, 2))
        past = float(np.clip(_sigmoid(2.5 * (price - urgency)) + rng.normal(0, 0.08), 0.0, 1.0))
        cid = f"C{i:05d}"
        customers[cid] = Customer(cid, drop, student, price, urgency, round(past, 4))
    customer_ids = list(customers)

    # partners
    partners: list[PartnerSeed] = []
    for i in range(spec.n_partners):
        anchor = food[int(rng.integers(len(food)))].location
        loc = _ring_point(rng, anchor, 0.0, 0.6)
        small = rng.random() < 0.1
        vehicle = Vehicle(30.0, 10.0, 1, False) if small else Vehicle()
        dropout = None
        p_drop = 1 - math.exp(-truth.partner_dropout_per_hour * spec.duration_min / 60.0)
        if rng.random() < p_drop:
            dropout = spec.start_at + float(rng.uniform(0, spec.duration_min))
        partners.append(PartnerSeed(f"P{i:03d}", loc, vehicle, dropout))

    # order stream (per-minute Poisson arrivals)
    orders: list[OrderRequest] = []
    recent: dict[str, deque[float]] = defaultdict(deque)
    for minute in range(int(spec.duration_min)):
        t0 = spec.start_at + minute
        hour = local_hour(t0, tz_offset_min)
        count = int(rng.poisson(demand_rate(spec, hour)))
        for placed_at in sorted(t0 + rng.uniform(0, 1, size=count)):
            cust = customers[customer_ids[int(rng.integers(len(customer_ids)))]]
            is_grocery = rng.random() < spec.grocery_share
            if is_grocery:
                store = min(grocery, key=lambda s: road_km(s.location, cust.drop, 1.0))
                category = Category.GROCERY
                items = int(rng.integers(2, 12))
                basket = round(float(items * rng.uniform(40, 120)), 2)
            else:
                dists = np.array([road_km(s.location, cust.drop, 1.0) for s in food])
                w = popularity * np.exp(-dists / 2.0)
                store = food[int(rng.choice(len(food), p=w / w.sum()))]
                category = Category.FOOD
                items = int(rng.integers(1, 5))
                basket = round(float(items * rng.uniform(120, 280)), 2)
            window = recent[store.id]
            while window and window[0] < placed_at - 20.0:
                window.popleft()
            load = len(window)
            window.append(float(placed_at))
            prep = true_prep_minutes(
                rng, truth, category, store.base_prep_min, items, load, local_hour(placed_at, tz_offset_min)
            )
            cancel = float(rng.uniform(1.0, 12.0)) if rng.random() < truth.cancel_prob else None
            orders.append(
                OrderRequest(
                    id=f"{spec.name[:2].upper()}{len(orders):05d}",
                    placed_at=float(placed_at),
                    customer_id=cust.id,
                    store_id=store.id,
                    category=category,
                    item_count=items,
                    basket_value=basket,
                    handling=generate_handling(rng, category, items),
                    store_load=load,
                    true_prep_min=round(prep, 4),
                    cancel_after_min=cancel,
                )
            )
    return Scenario(spec, stores, drop_points, customers, partners, orders)

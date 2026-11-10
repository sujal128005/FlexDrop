"""FlexDrop Studio: the browser prototype with a ready-made campus world.

``flexdrop studio`` builds a normal FlexDrop app, seeds it with the synthetic campus (stores,
hostel gates, delivery partners), generates a session API key and serves the UI at ``/app/``.
It is a development/demo mode: it refuses to run with ``FLEXDROP_ENV=production`` and always
uses its own database (a fresh temporary SQLite file unless one is given).
"""

from __future__ import annotations

import json
import math
import random
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from flexdrop.api.app import create_app
from flexdrop.api.security import generate_key, hash_key
from flexdrop.config import Settings
from flexdrop.domain.models import Category, GeoPoint
from flexdrop.schemas import Location, PartnerIn, StoreIn, VehicleIn
from flexdrop.services import catalog
from flexdrop.simulation.scenario import build_scenario, campus_spec

ZONE = "campus"

CAMPUS_NAME = "IIITDM Kurnool"
CITY = "Kurnool"
# Approximate location of the IIITDM Kurnool campus: Jagannathagattu hills, beside NH 40, south of
# Kurnool city (Andhra Pradesh). The projection origin of the 3D scene is this point.
CAMPUS_CENTER = GeoPoint(15.7623, 78.0357)

# ---------------------------------------------------------------- site plan (scene units)
# The Studio world is laid out on a plane: x points east, z points south, 1 unit = 8 m. The plan
# below is the single source of truth for both the API world (store and gate coordinates) and the
# 3D view. It follows the real arrangement: the campus has ONE entrance gate facing NH 40, the
# academic area with the clock-tower roundabout sits just inside the gate, the hostels are behind
# it towards the hills, and Kurnool city lies to the north along the highway. The real campus is
# about 12 km from the city centre; the Studio compresses that to about 3 km so a whole delivery
# fits on screen and inside the hot-food age limit.
UNIT_M = 8.0
M_PER_DEG = 111195.0
CITY_XS = [-255, -195, -135, -75, -15]  # north-south streets (x = -135 is NH 40)
CITY_ZS = [-490, -430, -370, -310, -250]  # east-west streets, north to south
STREET_W = 14
SLOT_SETBACK = 19.5  # building centre to street centre line
INSTAMART_SETBACK = 19.0
# Kurnool localities by city block (row = east-west street from the north, column = block from the west).
LOCALITY_GRID = [
    ["Old Town", "Old Town", "Budhawarpet", "Budhawarpet"],
    ["Old Town", "Kothapeta", "Budhawarpet", "Abdullah Khan Estate"],
    ["Sampath Nagar", "Raj Vihar Centre", "Raj Vihar Centre", "Gandhi Nagar"],
    ["Bellary Road", "Sampath Nagar", "Venkataramana Colony", "Gandhi Nagar"],
    ["Kallur", "STBC College Road", "Venkataramana Colony", "Nandyala Road"],
]
# (map label, display name, hall, building centre x, z, facing x, z). Each hall has Wing A and Wing B.
# West-side wings face east onto the hostel avenue (x = 48), east-side wings face west.
HOSTELS: list[tuple[str, str, str, float, float, float, float]] = [
    ("MVHR A", "MVHR Wing A", "MVHR", 32, -64, 1, 0),
    ("MVHR B", "MVHR Wing B", "MVHR", 32, -36, 1, 0),
    ("SRKH A", "SRKH Wing A", "SRKH", 64, -64, -1, 0),
    ("SRKH B", "SRKH Wing B", "SRKH", 64, -36, -1, 0),
    ("Kalam A", "Kalam Hall of Residence Wing A", "Kalam Hall of Residence", 32, 48, 1, 0),
    ("Kalam B", "Kalam Hall of Residence Wing B", "Kalam Hall of Residence", 32, 76, 1, 0),
    ("KC A", "Kalpana Chawla Hall of Residence Wing A", "Kalpana Chawla Hall of Residence", 64, 48, -1, 0),
    ("KC B", "Kalpana Chawla Hall of Residence Wing B", "Kalpana Chawla Hall of Residence", 64, 76, -1, 0),
]
HOSTEL_FRONT = 7.0  # handover point in front of a wing's entrance
# Student PGs on the village lane across the highway from the gate: (x, z, facing z).
PGS = [(-162, -52, 1), (-200, -52, 1), (-162, -18, -1), (-200, -18, -1)]
# Restaurant names are inspired by real Kurnool eateries with the LAST WORD CHANGED, so the demo
# never presents simulated orders, menus or timings as a real business's.
RESTAURANTS = [
    "Paradise Grill", "Ajwa Kitchen", "Hyderabadi's Biryani Corner", "Sri Divya Kunda Handi",
    "Kondareddy Ruchulu", "Mintleaf Diner", "Spicy House Family Dhaba", "Madhu Family Mess",
    "Hotel Hindustan Royal", "Lotus Bistro", "Srinu Idlis", "Amaravathi Tiffin Corner",
    "Forza Coffee", "Karachi Patisserie", "Meenakshi Rooftop Grill", "Pista Mahal",
    "Red Peppers", "Ajantha Tiffins and Snacks", "Udupi Durga Bhavan", "Taaza Dosa",
    "Sapthagiri Tiffins and Thali", "Kalasree Bhojanam", "RajaVilas Dhaba", "Khan Sahab's Darbar",
    "Sri Balaji Tiffins", "Taste Inn Family Diner",
]  # fmt: skip
NAME_NOTE = (
    "Restaurant names are inspired by real Kurnool eateries with the last word changed. "
    "Orders, menus, timings and riders are simulated."
)


def _ring(cx: float, cz: float, r: float, n: int = 24) -> list[list[float]]:
    return [
        [round(cx + r * math.sin(2 * math.pi * i / n), 3), round(cz + r * math.cos(2 * math.pi * i / n), 3)]
        for i in range(n)
    ]


def _roads() -> list[dict[str, Any]]:
    city = [
        {"id": f"ns{i}", "kind": "street", "width": STREET_W, "pts": [[x, CITY_ZS[0]], [x, CITY_ZS[-1]]]}
        for i, x in enumerate(CITY_XS)
        if x != -135
    ] + [
        {"id": f"ew{i}", "kind": "street", "width": STREET_W, "pts": [[CITY_XS[0], z], [CITY_XS[-1], z]]}
        for i, z in enumerate(CITY_ZS)
    ]
    return [
        {"id": "nh40", "name": "NH 40", "kind": "highway", "width": 16, "pts": [[-135, -640], [-135, 260]]},
        *city,
        {"id": "checkpost", "name": "Nandyal Checkpost", "kind": "street", "width": STREET_W,
         "pts": [[-205, -170], [-65, -170]]},
        {"id": "village", "name": "Jagannathagattu village road", "kind": "lane", "width": 10,
         "pts": [[-235, -35], [-135, -35]]},
        {"id": "gate_road", "name": "Campus entrance road", "kind": "campus", "width": 10,
         "pts": [[-135, -35], [-75, -35]]},
        {"id": "roundabout", "name": "Clock tower circle", "kind": "campus", "width": 7, "closed": True,
         "pts": _ring(-66, -35, 9)},
        {"id": "spine", "name": "Campus road", "kind": "campus", "width": 9, "pts": [[-66, -26], [-66, 22], [48, 22]]},
        {"id": "hostel_ave", "name": "Hostel avenue", "kind": "campus", "width": 9, "pts": [[48, -84], [48, 90]]},
    ]  # fmt: skip


def _city_slots() -> list[dict[str, Any]]:
    """Building plots along the east-west city streets, two per block side."""
    slots = []
    for row, z in enumerate(CITY_ZS):
        for col in range(len(CITY_XS) - 1):
            mid = (CITY_XS[col] + CITY_XS[col + 1]) / 2
            for side in (-1, 1):  # -1: north side of the street (faces south), 1: south side
                # the outer sides of the first and last streets face open land: keep them for houses too
                for k, dx in enumerate((-11, 11)):
                    slots.append(
                        {
                            "id": f"r{row}c{col}{'n' if side < 0 else 's'}{k}",
                            "row": row,
                            "col": col,
                            "side": side,
                            "x": mid + dx,
                            "z": z + side * SLOT_SETBACK,
                            "fx": 0,
                            "fz": -side,
                            "front": [mid + dx, z + side * (STREET_W / 2 - 0.5)],
                            "locality": LOCALITY_GRID[row][col],
                        }
                    )
    return slots


def site_plan() -> dict[str, Any]:
    return {
        "unit_m": UNIT_M,
        "campus": {"x0": -100, "x1": 100, "z0": -90, "z1": 95, "gate": [-100, -35]},
        # The campus sits on a hilltop plateau above the plain; the entrance road climbs to the gate.
        # Height is in scene units and exaggerated for readability (like the distances).
        "plateau": {"height": 20, "margin": 3, "slope_west": 23, "slope": 40},
        "roundabout": [-66, -35, 9],
        "roads": _roads(),
        "city": {"xs": CITY_XS, "zs": CITY_ZS, "center": [-135, -370]},
        "fort": [-225, -460],
        "temple": [178, -32],
        "river_z": [-600, -572],
        "checkpost": [-135, -170],
        "note": NAME_NOTE,
    }


def to_latlng(x: float, z: float, center: GeoPoint = CAMPUS_CENTER) -> GeoPoint:
    """Scene coordinates to latitude/longitude (inverse of the 3D view's projection)."""
    lat = center.lat - z * UNIT_M / M_PER_DEG
    lng = center.lng + x * UNIT_M / (M_PER_DEG * math.cos(math.radians(center.lat)))
    return GeoPoint(round(lat, 6), round(lng, 6))


# Menu items: (name, price INR, temperature class, volume litres, weight kg, extra flags)
FOOD_MENU: list[dict[str, Any]] = [
    {"name": "Veg Biryani", "price": 180, "temp": "HOT", "volume": 2.5, "weight": 0.6},
    {"name": "Chicken Roll", "price": 120, "temp": "HOT", "volume": 1.2, "weight": 0.3},
    {"name": "Masala Dosa", "price": 90, "temp": "HOT", "volume": 2.0, "weight": 0.4},
    {"name": "Paneer Thali", "price": 220, "temp": "HOT", "volume": 3.0, "weight": 0.8, "leak_risk": True},
    {"name": "Cold Coffee", "price": 110, "temp": "CHILLED", "volume": 0.6, "weight": 0.4},
    {"name": "Chocolate Brownie", "price": 80, "temp": "AMBIENT", "volume": 0.5, "weight": 0.2, "fragile": True},
]
GROCERY_MENU: list[dict[str, Any]] = [
    {"name": "Milk 1 L", "price": 60, "temp": "CHILLED", "volume": 1.2, "weight": 1.05},
    {"name": "Bread", "price": 45, "temp": "AMBIENT", "volume": 2.0, "weight": 0.4},
    {"name": "Ice-cream tub", "price": 250, "temp": "FROZEN", "volume": 1.0, "weight": 0.7},
    {"name": "Instant noodles x4", "price": 96, "temp": "AMBIENT", "volume": 1.5, "weight": 0.3},
    {"name": "Eggs x6", "price": 72, "temp": "AMBIENT", "volume": 1.0, "weight": 0.4, "fragile": True},
    {"name": "Bananas 1 kg", "price": 55, "temp": "AMBIENT", "volume": 2.0, "weight": 1.0},
]


@dataclass
class DemoWorld:
    api_key: str
    zone: str
    center: dict[str, float]
    gates: list[dict[str, Any]]
    stores: list[dict[str, Any]]
    started_at: str
    plan: dict[str, Any] = field(default_factory=dict)
    menus: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"FOOD": FOOD_MENU, "GROCERY": GROCERY_MENU})

    def session_payload(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key,
            "mode": "studio",
            "world": {
                "zone": self.zone,
                "campus": CAMPUS_NAME,
                "city": CITY,
                "center": self.center,
                "gates": self.gates,
                "stores": self.stores,
                "menus": self.menus,
                "started_at": self.started_at,
                "site_plan": self.plan,
                "note": NAME_NOTE,
            },
        }


IST = timezone(timedelta(hours=5, minutes=30))


def resolve_start(start: str, now: datetime | None = None) -> datetime:
    """Simulation start time.

    * ``now`` - the current time;
    * ``HH:MM`` - that IST time today (or yesterday if it is still in the future);
    * ``auto`` (default) - now during service hours (11:00-22:30 IST), otherwise the most recent
      19:30 IST dinner peak, so a demo run late at night still shows a busy campus.
    """
    now = now or datetime.now(tz=UTC)
    if start == "now":
        return now
    local = now.astimezone(IST)
    if start == "auto":
        minutes = local.hour * 60 + local.minute
        if 11 * 60 <= minutes < 22 * 60 + 30:
            return now
        start = "19:30"
    try:
        hh, mm = (int(x) for x in start.split(":"))
        target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError as exc:
        raise ValueError("start must be 'auto', 'now' or HH:MM (IST)") from exc
    if target > local:
        target -= timedelta(days=1)
    return target.astimezone(UTC)


def build_studio_app(
    model_dir: str = "models",
    database_url: str | None = None,
    reports_dir: str = "reports",
    n_partners: int = 40,
    seed: int = 3,
    start: str = "auto",
) -> tuple[FastAPI, DemoWorld, Callable[[], None]]:
    """Create the studio app. Returns (app, world, cleanup)."""
    sim_start = resolve_start(start)
    tmp_dir: str | None = None
    if database_url is None:
        tmp_dir = tempfile.mkdtemp(prefix="flexdrop-studio-")
        database_url = "sqlite:///" + (Path(tmp_dir) / "studio.db").as_posix()
    key = generate_key()
    settings = Settings(
        env="development",
        database_url=database_url,
        model_dir=model_dir,
        reports_dir=reports_dir,
        api_keys=json.dumps({hash_key(key): {"name": "studio", "roles": ["admin"]}}),
        log_json=False,
        log_level="WARNING",
        rate_limit_per_minute=100_000,
        cpsat_time_limit_s=1.0,  # keep interactive epochs snappy
    )
    app = create_app(settings)
    ctx = app.state.ctx
    # Timings (prep times, partner vehicles) come from the synthetic campus scenario; positions
    # come from the IIITDM Kurnool site plan. The simulation scenarios behind the reported
    # results are unchanged.
    sc = build_scenario(replace(campus_spec(seed=seed, duration_min=30), center=CAMPUS_CENTER))
    rng = random.Random(seed)
    plan = site_plan()

    def point(x: float, z: float) -> dict[str, float]:
        p = to_latlng(x, z)
        return {"lat": p.lat, "lng": p.lng}

    gates: list[dict[str, Any]] = [
        {
            "id": short,
            "name": name,
            "hall": hall,
            "on_campus": True,
            "x": x,
            "z": z,
            "fx": fx,
            "fz": fz,
            "front": [x + fx * HOSTEL_FRONT, z + fz * HOSTEL_FRONT],
            **point(x + fx * HOSTEL_FRONT, z + fz * HOSTEL_FRONT),
        }
        for short, name, hall, x, z, fx, fz in HOSTELS
    ]
    gates += [
        {
            "id": f"PG{i + 1}",
            "name": f"Student PG {i + 1}",
            "hall": None,
            "on_campus": False,
            "x": x,
            "z": z,
            "fx": 0,
            "fz": fz,
            "front": [x, z + fz * 7.5],
            **point(x, z + fz * 7.5),
        }
        for i, (x, z, fz) in enumerate(PGS)
    ]

    slots = _city_slots()
    reserved = {"r2c1s0", "r2c1s1", "r0c0s0", "r0c0s1", "r1c0n0", "r1c0n1"}  # Instamart, fort
    free = [sl for sl in slots if sl["id"] not in reserved]
    rng.shuffle(free)
    food_src = [st for st in sc.stores if st.category == Category.FOOD]
    grocery_src = [st for st in sc.stores if st.category == Category.GROCERY]
    placed: list[tuple[str, str, Category, float, dict[str, Any]]] = []
    for i, name in enumerate(RESTAURANTS):
        sl = free[i]
        placed.append((f"K{i + 1:02d}", name, Category.FOOD, food_src[i % len(food_src)].base_prep_min, sl))
    instamarts = [
        {"x": -165.0, "z": -370 + INSTAMART_SETBACK, "fx": 0, "fz": -1, "front": [-165.0, -370 + STREET_W / 2 - 0.5],
         "locality": "Raj Vihar Centre"},
        {"x": -100.0, "z": -170 - INSTAMART_SETBACK, "fx": 0, "fz": 1, "front": [-100.0, -170 - STREET_W / 2 + 0.5],
         "locality": "Nandyal Checkpost"},
    ]  # fmt: skip
    for i, im in enumerate(instamarts):
        prep = grocery_src[i % len(grocery_src)].base_prep_min if grocery_src else 4.0
        placed.append((f"IM{i + 1}", f"Instamart {im['locality']}", Category.GROCERY, prep, im))
    used = {sl["id"] for sl in free[: len(RESTAURANTS)]}
    plan["free_slots"] = [sl for sl in slots if sl["id"] not in used and sl["id"] not in reserved]

    now = sim_start
    stores_payload: list[dict[str, Any]] = []
    with ctx.db.session() as s:
        for store_id, name, category, prep, sl in placed:
            loc = to_latlng(sl["front"][0], sl["front"][1])
            catalog.upsert_store(
                s,
                StoreIn(
                    id=store_id,
                    name=name,
                    category=category,
                    location=Location(lat=loc.lat, lng=loc.lng),
                    base_prep_min=prep,
                    zone=ZONE,
                ),
                "studio",
            )
            stores_payload.append(
                {
                    "id": store_id,
                    "name": name,
                    "category": category.value,
                    "lat": loc.lat,
                    "lng": loc.lng,
                    "base_prep_min": prep,
                    "locality": sl["locality"],
                    "x": sl["x"],
                    "z": sl["z"],
                    "fx": sl["fx"],
                    "fz": sl["fz"],
                    "front": sl["front"],
                }
            )
        # Riders wait where the work is: on city streets, and every fifth at Nandyal Checkpost.
        for i, p in enumerate(sc.partners[:n_partners]):
            if i % 5 == 4:
                x, z = rng.uniform(-200, -70), -170.0
            elif rng.random() < 0.5:
                x, z = float(rng.choice(CITY_XS)), rng.uniform(CITY_ZS[0], CITY_ZS[-1])
            else:
                x, z = rng.uniform(CITY_XS[0], CITY_XS[-1]), float(rng.choice(CITY_ZS))
            loc = to_latlng(x, z)
            v = p.vehicle
            catalog.upsert_partner(
                s,
                PartnerIn(
                    id=p.id,
                    location=Location(lat=loc.lat, lng=loc.lng),
                    available_at=now,
                    vehicle=VehicleIn(
                        volume_l=v.volume_l, weight_kg=v.weight_kg, compartments=v.compartments, insulated=v.insulated
                    ),
                    zone=ZONE,
                ),
                "studio",
            )
    world = DemoWorld(
        api_key=key,
        zone=ZONE,
        center={"lat": CAMPUS_CENTER.lat, "lng": CAMPUS_CENTER.lng},
        gates=gates,
        stores=stores_payload,
        started_at=now.isoformat(),
        plan=plan,
    )
    app.state.demo = world

    def cleanup() -> None:
        ctx.db.dispose()
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return app, world, cleanup

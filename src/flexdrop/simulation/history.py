"""Historical (training) dataset generation from the scenario ground truth.

Produces CSV files used by :mod:`flexdrop.ml.training`:

* ``historical_prep.csv``   - preparation-time observations
* ``historical_trips.csv``  - travel-time observations
* ``offer_history.csv``     - flexible-offer impressions and accept/decline outcomes
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

from flexdrop.domain.models import Category
from flexdrop.simulation.scenario import Scenario, true_prep_minutes

PREP_COLUMNS = ["category", "store_base_prep", "item_count", "hour", "is_weekend", "store_load", "prep_minutes"]
TRIP_COLUMNS = ["road_km", "hour", "is_weekend", "minutes"]
OFFER_COLUMNS = ["reward", "extra_min", "past_rate", "is_student", "basket_value", "hour", "accepted"]


def _write(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)


def generate_history(
    scenario: Scenario,
    out_dir: str | Path,
    n_prep: int = 20000,
    n_trips: int = 20000,
    n_offers: int = 20000,
    seed: int = 7,
) -> dict[str, Path]:
    rng = np.random.default_rng(seed)
    truth = scenario.spec.truth
    out = Path(out_dir)

    prep_rows: list[list[object]] = []
    stores = scenario.stores
    for _ in range(n_prep):
        s = stores[int(rng.integers(len(stores)))]
        hour = float(rng.uniform(10, 24))
        items = int(rng.integers(1, 5)) if s.category == Category.FOOD else int(rng.integers(2, 12))
        load = int(rng.poisson(2.0))
        weekend = int(rng.random() < 2 / 7)
        prep = true_prep_minutes(rng, truth, s.category, s.base_prep_min, items, load, hour)
        prep_rows.append([s.category.value, s.base_prep_min, items, round(hour, 4), weekend, load, round(prep, 4)])

    trip_rows: list[list[object]] = []
    for _ in range(n_trips):
        km = float(rng.uniform(0.05, 12.0))
        hour = float(rng.uniform(0, 24))
        weekend = int(rng.random() < 2 / 7)
        minutes = truth.mean_travel_minutes(km, hour) * float(rng.lognormal(0.0, truth.travel_noise_sigma))
        trip_rows.append([round(km, 4), round(hour, 4), weekend, round(minutes, 4)])

    offer_rows: list[list[object]] = []
    customers = list(scenario.customers.values())
    for _ in range(n_offers):
        c = customers[int(rng.integers(len(customers)))]
        m = int(rng.choice([5, 10, 15]))
        r = int(rng.integers(0, 31))
        hour = float(rng.uniform(11, 24))
        basket = float(rng.uniform(150, 1200))
        u = truth.utility(r, m, c.urgency, c.price_sensitivity, c.is_student)
        accepted = int(rng.random() < 1.0 / (1.0 + math.exp(-u)))
        offer_rows.append([r, m, c.past_acceptance_rate, int(c.is_student), round(basket, 2), round(hour, 4), accepted])

    paths = {
        "prep": out / "historical_prep.csv",
        "trips": out / "historical_trips.csv",
        "offers": out / "offer_history.csv",
    }
    _write(paths["prep"], PREP_COLUMNS, prep_rows)
    _write(paths["trips"], TRIP_COLUMNS, trip_rows)
    _write(paths["offers"], OFFER_COLUMNS, offer_rows)
    return paths

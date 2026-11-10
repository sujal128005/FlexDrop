"""Export a synthetic scenario to CSV (sample data shipped in ``data/sample``)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from flexdrop.simulation.scenario import Scenario


def export_scenario(scenario: Scenario, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = scenario.spec.name
    paths = {
        "stores": out / f"{name}_stores.csv",
        "partners": out / f"{name}_partners.csv",
        "customers": out / f"{name}_customers.csv",
        "orders": out / f"{name}_orders.csv",
        "spec": out / f"{name}_scenario.json",
    }
    with paths["stores"].open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "category", "lat", "lng", "base_prep_min", "zone"])
        for s in scenario.stores:
            w.writerow([s.id, s.name, s.category.value, s.location.lat, s.location.lng, s.base_prep_min, name])
    with paths["partners"].open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lat", "lng", "volume_l", "weight_kg", "compartments", "insulated", "dropout_at_min"])
        for p in scenario.partners:
            drop = round(p.dropout_at - scenario.start_at, 3) if p.dropout_at is not None else ""
            v = p.vehicle
            w.writerow(
                [p.id, p.location.lat, p.location.lng, v.volume_l, v.weight_kg, v.compartments, int(v.insulated), drop]
            )
    with paths["customers"].open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "drop_lat", "drop_lng", "is_student", "price_sensitivity", "urgency", "past_acceptance_rate"])
        for c in scenario.customers.values():
            w.writerow(
                [
                    c.id,
                    c.drop.lat,
                    c.drop.lng,
                    int(c.is_student),
                    round(c.price_sensitivity, 4),
                    round(c.urgency, 4),
                    c.past_acceptance_rate,
                ]
            )
    with paths["orders"].open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "placed_at_min",
                "customer_id",
                "store_id",
                "category",
                "item_count",
                "basket_value",
                "temp_classes",
                "volume_l",
                "weight_kg",
                "leak_risk",
                "raw_meat",
                "sealed",
                "fragile",
                "store_load",
                "true_prep_min",
                "cancel_after_min",
            ]
        )
        for o in scenario.orders:
            h = o.handling
            w.writerow(
                [
                    o.id,
                    round(o.placed_at - scenario.start_at, 4),
                    o.customer_id,
                    o.store_id,
                    o.category.value,
                    o.item_count,
                    o.basket_value,
                    "|".join(sorted(t.value for t in h.temp_classes)),
                    h.volume_l,
                    h.weight_kg,
                    int(h.leak_risk),
                    int(h.raw_meat),
                    int(h.sealed),
                    int(h.fragile),
                    o.store_load,
                    o.true_prep_min,
                    o.cancel_after_min if o.cancel_after_min is not None else "",
                ]
            )
    spec = scenario.spec
    paths["spec"].write_text(
        json.dumps(
            {
                "name": spec.name,
                "center": {"lat": spec.center.lat, "lng": spec.center.lng},
                "start": spec.start_iso,
                "duration_min": spec.duration_min,
                "seed": spec.seed,
                "n_orders": len(scenario.orders),
                "n_partners": len(scenario.partners),
                "n_stores": len(scenario.stores),
                "demand_profile_orders_per_min": [list(x) for x in spec.demand_profile],
                "grocery_share": spec.grocery_share,
                "student_share": spec.student_share,
            },
            indent=2,
        )
    )
    return paths

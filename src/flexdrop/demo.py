"""Scripted end-to-end demonstration through the real HTTP API (in-process client).

Flow on a rolling timeline: register stores and partners -> orders arrive (offers quoted,
customers choose) -> the optimiser runs every 3 minutes of simulated time -> dispatch accepts
plans -> a preparation delay triggers continuous re-optimisation -> pickups and deliveries are
reported -> KPIs are read from ``/v1/metrics``.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

with warnings.catch_warnings():  # newer Starlette prefers httpx2; httpx remains supported
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from flexdrop.api.app import create_app
from flexdrop.api.security import generate_key, hash_key
from flexdrop.clock import from_minutes
from flexdrop.config import Settings
from flexdrop.simulation.scenario import OrderRequest, Scenario, build_scenario, campus_spec

N_PARTNERS = 30
EPOCH_MIN = 3.0


def _create_order(
    client: TestClient, headers: dict[str, str], sc: Scenario, req: OrderRequest, rnd: random.Random
) -> tuple[int, int]:
    """Create one order through the API and record the simulated customer's choice."""
    cust = sc.customers[req.customer_id]
    hd = req.handling
    r = client.post(
        "/v1/orders",
        headers={**headers, "Idempotency-Key": f"order-{req.id}"},
        json={
            "id": req.id,
            "store_id": req.store_id,
            "customer_id": req.customer_id,
            "drop": {"lat": cust.drop.lat, "lng": cust.drop.lng},
            "placed_at": from_minutes(req.placed_at).isoformat(),
            "item_count": req.item_count,
            "basket_value": req.basket_value,
            "handling": {
                "temp_classes": sorted(t.value for t in hd.temp_classes),
                "volume_l": hd.volume_l,
                "weight_kg": hd.weight_kg,
                "leak_risk": hd.leak_risk,
                "raw_meat": hd.raw_meat,
                "sealed": hd.sealed,
                "fragile": hd.fragile,
            },
            "customer": {"past_flex_acceptance_rate": cust.past_acceptance_rate, "is_student": cust.is_student},
        },
    )
    if r.status_code == 409:
        raise RuntimeError(
            f"order {req.id} already exists in this database; run the demo without --database-url "
            "(a fresh temporary database is used) or point it at an empty database"
        )
    r.raise_for_status()
    offers = r.json()["offers"]
    if not offers:
        return 0, 0
    accept = rnd.random() < 0.6
    offer = max(offers, key=lambda o: o["reward"] / o["extra_minutes"])
    client.post(
        "/v1/preferences",
        headers=headers,
        json={
            "order_id": req.id,
            "decision": "ACCEPT_FLEX" if accept else "STANDARD",
            "offer_id": offer["offer_id"] if accept else None,
        },
    ).raise_for_status()
    return 1, int(accept)


def _execute_plan(client: TestClient, headers: dict[str, str], plan: dict[str, Any], delay: bool) -> str | None:
    """Accept a plan and report its stops; optionally inject a preparation delay first."""
    pid = plan["plan_id"]
    client.post(f"/v1/plans/{pid}/events", headers=headers, json={"type": "DISPATCH_ACCEPTED"}).raise_for_status()
    current = client.get(f"/v1/plans/{pid}", headers=headers).json()
    action = None
    if delay:
        first = next(s for s in current["stops"] if s["type"] == "PICKUP")
        r = client.post(
            f"/v1/plans/{pid}/events",
            headers=headers,
            json={
                "type": "PREP_DELAY",
                "order_id": first["order_id"],
                "new_ready_at": first["etd"],
                "at": current["start_at"],
            },
        )
        r.raise_for_status()
        action = r.json()["action"]
        current = r.json()["plan"]
        if current["status"] in ("CANCELLED", "COMPLETED", "REJECTED"):
            return action  # every order was released back to the pool by the CRE
    for stop in current["stops"]:
        event_type = "PICKED_UP" if stop["type"] == "PICKUP" else "DELIVERED"
        client.post(
            f"/v1/plans/{pid}/events",
            headers=headers,
            json={"type": event_type, "order_id": stop["order_id"], "at": stop["eta"]},
        ).raise_for_status()
    return action


def run_demo(database_url: str | None, model_dir: str, n_orders: int = 24, seed: int = 3) -> dict[str, Any]:
    """Run the scripted demo. With ``database_url=None`` a fresh temporary SQLite database is
    used (and removed afterwards), so the demo can be re-run any number of times."""
    tmp_dir: str | None = None
    if database_url is None:
        tmp_dir = tempfile.mkdtemp(prefix="flexdrop-demo-")
        database_url = "sqlite:///" + (Path(tmp_dir) / "demo.db").as_posix()
    try:
        return _run_demo(database_url, model_dir, n_orders, seed)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_demo(database_url: str, model_dir: str, n_orders: int, seed: int) -> dict[str, Any]:
    key = generate_key()
    settings = Settings(
        database_url=database_url,
        model_dir=model_dir,
        api_keys=json.dumps({hash_key(key): {"name": "demo-admin", "roles": ["admin"]}}),
        log_json=False,
        log_level="WARNING",
    )
    app = create_app(settings)
    sc = build_scenario(campus_spec(seed=seed, duration_min=30))
    rnd = random.Random(seed)  # noqa: S311 - demo data only
    headers = {"X-API-Key": key}
    log: list[str] = []

    with TestClient(app) as client:
        for s in sc.stores:
            client.post(
                "/v1/stores",
                headers=headers,
                json={
                    "id": s.id,
                    "name": s.name,
                    "category": s.category.value,
                    "location": {"lat": s.location.lat, "lng": s.location.lng},
                    "base_prep_min": s.base_prep_min,
                    "zone": "campus",
                },
            ).raise_for_status()
        for p in sc.partners[:N_PARTNERS]:
            v = p.vehicle
            client.post(
                "/v1/partners",
                headers=headers,
                json={
                    "id": p.id,
                    "location": {"lat": p.location.lat, "lng": p.location.lng},
                    "status": "IDLE",
                    "available_at": from_minutes(sc.start_at).isoformat(),
                    "vehicle": {
                        "volume_l": v.volume_l,
                        "weight_kg": v.weight_kg,
                        "compartments": v.compartments,
                        "insulated": v.insulated,
                    },
                    "zone": "campus",
                },
            ).raise_for_status()
        log.append(f"registered {len(sc.stores)} stores and {N_PARTNERS} partners")

        reqs = sc.orders[:n_orders]
        offered = accepted = 0
        plans: list[dict[str, Any]] = []
        t = reqs[0].placed_at + EPOCH_MIN
        remaining = list(reqs)
        drain_epochs = 0
        while remaining or drain_epochs < 6:
            batch = [r for r in remaining if r.placed_at < t]
            remaining = [r for r in remaining if r.placed_at >= t]
            for req in batch:
                o, a = _create_order(client, headers, sc, req, rnd)
                offered += o
                accepted += a
            r = client.post(
                "/v1/optimize", headers=headers, json={"zone": "campus", "as_of": from_minutes(t).isoformat()}
            )
            r.raise_for_status()
            out = r.json()
            plans.extend(out["plans"])
            log.append(
                f"t+{t - reqs[0].placed_at:4.0f} min  new orders={len(batch):2d}  plans={len(out['plans']):2d}  "
                f"held={len(out['held_order_ids'])}  deferred={len(out['deferred_order_ids'])}  "
                f"status={out['status']}  latency={out['latency_ms']:.0f} ms"
            )
            t += EPOCH_MIN
            if not remaining:
                drain_epochs += 1
        log.insert(1, f"created {len(reqs)} orders; {offered} received offers; {accepted} accepted flexible delivery")
        multi = [p for p in plans if len(p["order_ids"]) > 1]
        log.append(
            f"{len(plans)} plans, {len(multi)} coordinated (multi-order); expected savings vs solo baseline "
            f"INR {sum(p['expected_savings'] for p in plans):.0f}"
        )

        delay_target = multi[0]["plan_id"] if multi else (plans[0]["plan_id"] if plans else None)
        for plan in plans:
            action = _execute_plan(client, headers, plan, delay=plan["plan_id"] == delay_target)
            if action:
                log.append(f"preparation delay injected on plan {plan['plan_id'][:8]} -> CRE action {action}")

        metrics = client.get("/v1/metrics", headers=headers, params={"window_hours": 744}).json()["values"]
        log.append(
            "metrics: delivered={orders_delivered} on_time={on_time_rate} coordinated_share={coordinated_share} "
            "offer_acceptance={offer_acceptance_rate} avg_click_to_door={avg_click_to_door_min} min".format(**metrics)
        )
    return {"log": log, "metrics": metrics, "plans": plans}

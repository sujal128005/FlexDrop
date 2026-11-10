from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from flexdrop.api.app import create_app
from flexdrop.db.models import AuditLogRow, FlexOfferRow, OrderRow
from flexdrop.geo import offset
from tests.conftest import CENTER, api_settings, headers

STORE = {
    "id": "S1",
    "name": "Campus Biryani",
    "category": "FOOD",
    "location": {"lat": CENTER.lat, "lng": CENTER.lng},
    "base_prep_min": 10,
    "zone": "campus",
}
HOSTEL = offset(CENTER, 1.2, 0.3)


def now() -> datetime:
    return datetime.now(tz=UTC).replace(microsecond=0)


def order_payload(oid: str, placed: datetime, **over: Any) -> dict[str, Any]:
    body = {
        "id": oid,
        "store_id": "S1",
        "customer_id": f"student-{oid}",
        "drop": {"lat": HOSTEL.lat, "lng": HOSTEL.lng},
        "placed_at": placed.isoformat(),
        "item_count": 2,
        "basket_value": 450,
        "handling": {"temp_classes": ["HOT"], "volume_l": 6, "weight_kg": 1.2},
        "customer": {"past_flex_acceptance_rate": 0.8, "is_student": True},
    }
    body.update(over)
    return body


def setup_world(client: TestClient, n_partners: int = 3) -> None:
    assert client.post("/v1/stores", json=STORE, headers=headers("dispatch")).status_code == 200
    for i in range(n_partners):
        loc = offset(CENTER, 0.2 * i, 0.1)
        r = client.post(
            "/v1/partners",
            json={
                "id": f"P{i}",
                "location": {"lat": loc.lat, "lng": loc.lng},
                "zone": "campus",
                "available_at": (now() - timedelta(minutes=30)).isoformat(),
            },
            headers=headers("dispatch"),
        )
        assert r.status_code == 200, r.text


def create_orders(client: TestClient, n: int, placed: datetime) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        r = client.post("/v1/orders", json=order_payload(f"O{i}", placed), headers=headers("checkout"))
        assert r.status_code == 201, r.text
        out.append(r.json())
    return out


def optimize(client: TestClient, as_of: datetime, **extra: Any) -> dict[str, Any]:
    r = client.post("/v1/optimize", json={"as_of": as_of.isoformat(), **extra}, headers=headers("dispatch"))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ health & security
def test_health_ready_and_request_id(client) -> None:  # noqa: ANN001
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    ready = client.get("/ready").json()
    assert ready["status"] == "ready" and ready["models"]["prep"] == "analytic-prep-v1"
    assert client.get("/nope").json()["error"]["code"] == "not_found"


def test_authentication_and_authorisation(client) -> None:  # noqa: ANN001
    assert client.post("/v1/stores", json=STORE).status_code == 401
    assert client.post("/v1/stores", json=STORE, headers={"X-API-Key": "wrong"}).json()["error"]["code"] == (
        "unauthenticated"
    )
    r = client.post("/v1/stores", json=STORE, headers=headers("checkout"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.post("/v1/stores", json=STORE, headers=headers("admin")).status_code == 200
    assert client.get("/v1/metrics", headers=headers("checkout")).status_code == 403
    assert client.get("/v1/metrics", headers=headers("analyst")).status_code == 200


def test_validation_errors(client) -> None:  # noqa: ANN001
    bad = {**STORE, "location": {"lat": 100, "lng": 0}}
    r = client.post("/v1/stores", json=bad, headers=headers("dispatch"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.post("/v1/stores", json={**STORE, "extra": 1}, headers=headers("dispatch")).status_code == 422
    assert client.post("/v1/stores", json={**STORE, "id": "bad id!"}, headers=headers("dispatch")).status_code == 422
    setup_world(client)
    naive = order_payload("X", now())
    naive["placed_at"] = "2026-11-09T10:00:00"
    assert client.post("/v1/orders", json=naive, headers=headers("checkout")).status_code == 422
    empty_temp = order_payload("Y", now(), handling={"temp_classes": [], "volume_l": 1, "weight_kg": 1})
    assert client.post("/v1/orders", json=empty_temp, headers=headers("checkout")).status_code == 422


def test_rate_limit_and_body_limit(tmp_path) -> None:  # noqa: ANN001
    app = create_app(api_settings(tmp_path, rate_limit_per_minute=3, max_request_bytes=2048))
    with TestClient(app) as c:
        codes = [c.get("/v1/stores/S1", headers=headers("dispatch")).status_code for _ in range(4)]
        assert codes[:3] == [404, 404, 404] and codes[3] == 429
        r = c.get("/v1/stores/S1", headers=headers("dispatch"))
        assert r.headers.get("Retry-After")
        big = c.post(
            "/v1/stores", content=b"x" * 5000, headers={**headers("admin"), "Content-Type": "application/json"}
        )
        assert big.status_code == 413


def test_prometheus_endpoint_protected(client) -> None:  # noqa: ANN001
    assert client.get("/metrics/prometheus").status_code == 401
    assert client.get("/metrics/prometheus", headers=headers("dispatch")).status_code == 403
    r = client.get("/metrics/prometheus", headers=headers("analyst"))
    assert r.status_code == 200 and "flexdrop_http_requests_total" in r.text


# ------------------------------------------------------------------ catalog
def test_store_and_partner_upsert(client) -> None:  # noqa: ANN001
    setup_world(client, 1)
    assert client.get("/v1/stores/S1", headers=headers("checkout")).json()["name"] == "Campus Biryani"
    assert client.get("/v1/stores/S9", headers=headers("checkout")).status_code == 404
    p = client.get("/v1/partners/P0", headers=headers("dispatch")).json()
    assert p["status"] == "IDLE" and p["vehicle"]["compartments"] == 2
    r = client.post(
        "/v1/partners",
        json={"id": "P0", "location": {"lat": 12.9, "lng": 77.6}, "status": "ASSIGNED"},
        headers=headers("dispatch"),
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ orders & consent
def test_order_offers_and_consent_flow(client, tmp_path) -> None:  # noqa: ANN001
    setup_world(client)
    placed = now()
    orders = create_orders(client, 6, placed)
    first = orders[0]
    assert first["status"] == "PENDING" and first["mode"] == "STANDARD"
    assert first["offers_unavailable_reason"] == "no_economically_viable_offer"  # empty area at first
    with_offers = [o for o in orders if o["offers"]]
    assert with_offers, "dense hostel demand should create offers"
    o = with_offers[0]
    offer = o["offers"][0]
    assert offer["reward"] > 0 and offer["extra_minutes"] in (5, 10, 15)

    assert (
        client.post(
            "/v1/preferences", json={"order_id": o["id"], "decision": "ACCEPT_FLEX"}, headers=headers("checkout")
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/preferences",
            json={"order_id": o["id"], "decision": "ACCEPT_FLEX", "offer_id": "nope"},
            headers=headers("checkout"),
        ).status_code
        == 404
    )
    r = client.post(
        "/v1/preferences",
        json={"order_id": o["id"], "decision": "ACCEPT_FLEX", "offer_id": offer["offer_id"]},
        headers=headers("checkout"),
    )
    assert r.status_code == 200, r.text
    pref = r.json()
    assert pref["mode"] == "FLEX" and pref["max_extra_wait"] == offer["extra_minutes"]
    latest = datetime.fromisoformat(pref["latest_delivery_at"])
    std = datetime.fromisoformat(pref["standard_eta"])
    assert latest - std == timedelta(minutes=offer["extra_minutes"])
    again = client.post(
        "/v1/preferences", json={"order_id": o["id"], "decision": "STANDARD"}, headers=headers("checkout")
    )
    assert again.status_code == 409

    std_r = client.post(
        "/v1/preferences", json={"order_id": orders[1]["id"], "decision": "STANDARD"}, headers=headers("checkout")
    )
    assert std_r.status_code == 200 and std_r.json()["max_extra_wait"] == 0

    got = client.get(f"/v1/orders/{o['id']}", headers=headers("dispatch")).json()
    assert got["reward_status"] == "PENDING" and got["offers"]

    ctx = client.app.state.ctx
    with ctx.db.session() as s:
        row = s.get(OrderRow, o["id"])
        assert row.customer_ref != f"student-{o['id']}" and len(row.customer_ref) == 64  # pseudonymised
        assert s.query(AuditLogRow).filter_by(action="consent.recorded").count() == 2


def test_expired_offer_and_duplicates(client) -> None:  # noqa: ANN001
    setup_world(client)
    orders = create_orders(client, 6, now())
    o = next(x for x in orders if x["offers"])
    ctx = client.app.state.ctx
    with ctx.db.session() as s:
        for off in s.query(FlexOfferRow).filter_by(order_id=o["id"]):
            off.expires_at = now() - timedelta(minutes=1)
    r = client.post(
        "/v1/preferences",
        json={"order_id": o["id"], "decision": "ACCEPT_FLEX", "offer_id": o["offers"][0]["offer_id"]},
        headers=headers("checkout"),
    )
    assert r.status_code == 409 and "expired" in r.json()["error"]["message"]
    dup = client.post("/v1/orders", json=order_payload("O0", now()), headers=headers("checkout"))
    assert dup.status_code == 409
    unknown = client.post("/v1/orders", json=order_payload("Z", now(), store_id="NOPE"), headers=headers("checkout"))
    assert unknown.status_code == 404


# ------------------------------------------------------------------ optimisation & plans
def test_optimize_dry_run_and_idempotency(client) -> None:  # noqa: ANN001
    setup_world(client)
    placed = now()
    create_orders(client, 3, placed)
    as_of = placed + timedelta(minutes=8)
    dry = optimize(client, as_of, dry_run=True)
    assert dry["dry_run"] and all(p["plan_id"] is None for p in dry["plans"])
    assert client.get("/v1/orders/O0", headers=headers("dispatch")).json()["status"] == "PENDING"

    h = headers("dispatch", **{"Idempotency-Key": "run-1"})
    body = {"as_of": as_of.isoformat()}
    r1 = client.post("/v1/optimize", json=body, headers=h)
    r2 = client.post("/v1/optimize", json=body, headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["run_id"] == r2.json()["run_id"] and r2.headers.get("Idempotent-Replayed") == "true"
    r3 = client.post("/v1/optimize", json={"as_of": (as_of + timedelta(minutes=1)).isoformat()}, headers=h)
    assert r3.status_code == 409
    plans = r1.json()["plans"]
    assert plans, r1.json()
    planned = {oid for p in plans for oid in p["order_ids"]}
    for oid in planned:
        assert client.get(f"/v1/orders/{oid}", headers=headers("dispatch")).json()["status"] == "PLANNED"
    p0 = plans[0]
    assert client.get(f"/v1/partners/{p0['partner_id']}", headers=headers("dispatch")).json()["status"] == "ASSIGNED"
    assert p0["stops"][0]["type"] == "PICKUP" and p0["cost_total"] > 0
    assert set(p0["flex_allocation"]) == set(p0["order_ids"])


def _plan_with_orders(client: TestClient, n_orders: int = 2) -> tuple[dict[str, Any], datetime]:
    setup_world(client, n_partners=1)
    placed = now()
    create_orders(client, n_orders, placed)
    as_of = placed + timedelta(minutes=8)
    out = optimize(client, as_of)
    assert out["plans"], out
    return out["plans"][0], as_of


def test_plan_lifecycle_to_completion(client) -> None:  # noqa: ANN001
    plan, as_of = _plan_with_orders(client)
    pid = plan["plan_id"]
    ev = lambda body: client.post(f"/v1/plans/{pid}/events", json=body, headers=headers("dispatch"))  # noqa: E731
    assert ev({"type": "DISPATCH_ACCEPTED"}).json()["plan"]["status"] == "ACCEPTED"
    assert ev({"type": "DISPATCH_ACCEPTED"}).status_code == 409
    assert ev({"type": "PICKED_UP"}).status_code == 422  # order_id required
    assert ev({"type": "PICKED_UP", "order_id": "NOT-IN-PLAN"}).status_code == 404
    last = None
    for stop in plan["stops"]:
        t = "PICKED_UP" if stop["type"] == "PICKUP" else "DELIVERED"
        last = ev({"type": t, "order_id": stop["order_id"], "at": stop["eta"]})
        assert last.status_code == 200, last.text
    assert last is not None and last.json()["action"] == "COMPLETED"
    assert last.json()["plan"]["status"] == "COMPLETED"
    assert ev({"type": "DISPATCH_ACCEPTED"}).status_code == 409  # terminal
    partner = client.get(f"/v1/partners/{plan['partner_id']}", headers=headers("dispatch")).json()
    assert partner["status"] == "IDLE"
    m = client.get("/v1/metrics", headers=headers("analyst")).json()["values"]
    assert m["orders_delivered"] == len(plan["order_ids"]) and m["plans_completed"] == 1
    assert m["on_time_rate"] is not None


def test_cancellation_and_pickup_guards(client) -> None:  # noqa: ANN001
    plan, _ = _plan_with_orders(client)
    pid = plan["plan_id"]
    oid = plan["order_ids"][0]
    r = client.post(
        f"/v1/plans/{pid}/events", json={"type": "ORDER_CANCELLED", "order_id": oid}, headers=headers("dispatch")
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/v1/orders/{oid}", headers=headers("dispatch")).json()["status"] == "CANCELLED"
    body = r.json()
    if len(plan["order_ids"]) == 1:
        assert body["plan"]["status"] == "CANCELLED"
    else:
        assert oid not in body["plan"]["order_ids"] and body["plan"]["version"] == 2
        other = body["plan"]["order_ids"][0]
        pick = client.post(
            f"/v1/plans/{pid}/events", json={"type": "PICKED_UP", "order_id": other}, headers=headers("dispatch")
        )
        assert pick.status_code == 200
        late_cancel = client.post(
            f"/v1/plans/{pid}/events", json={"type": "ORDER_CANCELLED", "order_id": other}, headers=headers("dispatch")
        )
        assert late_cancel.status_code == 409


def test_prep_delay_triggers_reoptimisation(client) -> None:  # noqa: ANN001
    plan, as_of = _plan_with_orders(client)
    pid = plan["plan_id"]
    oid = plan["order_ids"][0]
    missing = client.post(
        f"/v1/plans/{pid}/events", json={"type": "PREP_DELAY", "order_id": oid}, headers=headers("dispatch")
    )
    assert missing.status_code == 422
    r = client.post(
        f"/v1/plans/{pid}/events",
        json={
            "type": "PREP_DELAY",
            "order_id": oid,
            "new_ready_at": (as_of + timedelta(minutes=60)).isoformat(),
            "at": as_of.isoformat(),
        },
        headers=headers("dispatch"),
    )
    assert r.status_code == 200, r.text
    action = r.json()["action"]
    assert action in ("SPLIT", "RELEASED_ALL", "RESEQUENCED", "UNCHANGED")
    if action in ("SPLIT", "RELEASED_ALL"):
        assert oid in r.json()["released_order_ids"]
        assert client.get(f"/v1/orders/{oid}", headers=headers("dispatch")).json()["status"] == "PENDING"


def test_partner_unavailable_releases_orders(client) -> None:  # noqa: ANN001
    plan, _ = _plan_with_orders(client)
    pid = plan["plan_id"]
    r = client.post(f"/v1/plans/{pid}/events", json={"type": "PARTNER_UNAVAILABLE"}, headers=headers("dispatch"))
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "RELEASED_ALL" and sorted(body["released_order_ids"]) == sorted(plan["order_ids"])
    assert body["plan"]["status"] == "CANCELLED"
    p = client.get(f"/v1/partners/{plan['partner_id']}", headers=headers("dispatch")).json()
    assert p["status"] == "OFFLINE"


def test_dispatch_rejection_releases(client) -> None:  # noqa: ANN001
    plan, _ = _plan_with_orders(client)
    r = client.post(
        f"/v1/plans/{plan['plan_id']}/events", json={"type": "DISPATCH_REJECTED"}, headers=headers("dispatch")
    )
    assert r.status_code == 200 and r.json()["action"] == "REJECTED"
    for oid in plan["order_ids"]:
        assert client.get(f"/v1/orders/{oid}", headers=headers("dispatch")).json()["status"] == "PENDING"
    assert client.get("/v1/plans/does-not-exist", headers=headers("dispatch")).status_code == 404


def test_kill_switch(client) -> None:  # noqa: ANN001
    setup_world(client)
    assert client.get("/v1/admin/kill-switch", headers=headers("dispatch")).status_code == 403
    r = client.post("/v1/admin/kill-switch", json={"enabled": True, "reason": "incident 42"}, headers=headers("admin"))
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["reason"] == "incident 42"
    orders = create_orders(client, 6, now())
    assert all(not o["offers"] and o["offers_unavailable_reason"] == "flexdrop_disabled" for o in orders)
    out = optimize(client, now() + timedelta(minutes=8))
    assert out["mode"] == "FALLBACK_KILL_SWITCH" and out["plans"] == []
    client.post("/v1/admin/kill-switch", json={"enabled": False, "reason": "resolved"}, headers=headers("admin"))
    out2 = optimize(client, now() + timedelta(minutes=8))
    assert out2["mode"] == "FLEXDROP"
    m = client.get("/v1/metrics", headers=headers("analyst")).json()["values"]
    assert m["optimization_fallbacks"] == 1 and m["kill_switch_enabled"] is False


def test_optimizer_failure_falls_back(client, monkeypatch) -> None:  # noqa: ANN001
    setup_world(client)
    create_orders(client, 2, now())
    service = client.app.state.dispatch_service

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(service.optimizer, "optimize", boom)
    out = optimize(client, now() + timedelta(minutes=8))
    assert out["mode"] == "FALLBACK_ERROR" and out["plans"] == []
    assert client.get("/v1/orders/O0", headers=headers("dispatch")).json()["status"] == "PENDING"


@pytest.mark.parametrize("path", ["/v1/stores", "/v1/partners", "/v1/orders", "/v1/preferences", "/v1/optimize"])
def test_post_endpoints_require_auth(client, path) -> None:  # noqa: ANN001
    assert client.post(path, json={}).status_code == 401


def test_docs_page_gets_relaxed_csp(client) -> None:  # noqa: ANN001
    r = client.get("/docs")
    assert r.status_code == 200 and "cdn.jsdelivr.net" in r.headers["Content-Security-Policy"]
    assert client.get("/openapi.json").json()["info"]["title"] == "FlexDrop API"


def test_production_disables_docs(tmp_path) -> None:  # noqa: ANN001
    import json as _json

    from flexdrop.api.security import hash_key

    settings = api_settings(
        tmp_path,
        env="production",
        pseudonymization_secret="p" * 40,
        api_keys=_json.dumps({hash_key("k"): {"name": "a", "roles": ["admin"]}}),
    )
    from flexdrop.db.session import Database

    db = Database(settings.database_url)
    db.create_all()  # production never auto-creates the schema
    db.dispose()
    with TestClient(create_app(settings)) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/health").status_code == 200


def test_cancel_pending_and_planned_orders(client) -> None:  # noqa: ANN001
    setup_world(client, n_partners=1)
    placed = now()
    create_orders(client, 3, placed)
    r = client.post("/v1/orders/O2/cancel", json={"reason": "changed mind"}, headers=headers("checkout"))
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert client.post("/v1/orders/O2/cancel", json={}, headers=headers("checkout")).status_code == 409
    assert client.post("/v1/orders/NOPE/cancel", json={}, headers=headers("checkout")).status_code == 404
    out = optimize(client, placed + timedelta(minutes=8))
    planned = {oid for p in out["plans"] for oid in p["order_ids"]}
    assert "O2" not in planned and planned
    target = sorted(planned)[0]
    r2 = client.post(
        f"/v1/orders/{target}/cancel",
        json={"at": (placed + timedelta(minutes=8)).isoformat()},
        headers=headers("dispatch"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "CANCELLED"
    plan = client.get(f"/v1/plans/{out['plans'][0]['plan_id']}", headers=headers("dispatch")).json()
    assert target not in plan["order_ids"] or plan["status"] == "CANCELLED"

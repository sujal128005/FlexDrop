from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime, timedelta

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from flexdrop.api.app import create_app
from flexdrop.cli import main
from flexdrop.studio import IST, build_studio_app, resolve_start
from tests.conftest import api_settings, headers


@pytest.fixture
def studio(tmp_path):  # noqa: ANN001, ANN201
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "campus_metrics.json").write_text(json.dumps({"independent": {"fulfilment_cost_per_order": 80.0}}))
    (reports / "robustness.json").write_text(json.dumps({"campus": {"flexdrop": {"cost_change_pct_mean": -15.0}}}))
    app, world, cleanup = build_studio_app(
        model_dir=str(tmp_path / "no-models"), reports_dir=str(reports), n_partners=12
    )
    with TestClient(app) as client:
        yield client, world
    cleanup()


def test_studio_session_and_seeded_world(studio) -> None:  # noqa: ANN001
    client, world = studio
    r = client.get("/app/session")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "studio" and body["api_key"] == world.api_key
    gates = body["world"]["gates"]
    assert body["world"]["campus"] == "IIITDM Kurnool" and body["world"]["city"] == "Kurnool"
    assert [g["id"] for g in gates if g["on_campus"]] == [
        "MVHR A",
        "MVHR B",
        "SRKH A",
        "SRKH B",
        "Kalam A",
        "Kalam B",
        "KC A",
        "KC B",
    ]
    assert len(gates) == 12 and all(s.get("locality") for s in body["world"]["stores"])
    assert {"FOOD", "GROCERY"} == set(body["world"]["menus"])
    plan = body["world"]["site_plan"]
    assert plan["campus"]["gate"] == [-100, -35] and "last word changed" in body["world"]["note"]
    # one entrance: the only road that crosses the campus wall is the gate road, at the gate
    c = plan["campus"]

    def crosses_wall(pts: list[list[float]]) -> bool:
        inside = [c["x0"] < x < c["x1"] and c["z0"] < z < c["z1"] for x, z in pts]
        return any(inside) and not all(inside)

    crossing = [r["id"] for r in plan["roads"] if crosses_wall(r["pts"])]
    assert crossing == ["gate_road"]
    stores = body["world"]["stores"]
    assert sum(s["category"] == "FOOD" for s in stores) == 26 and sum(s["category"] == "GROCERY" for s in stores) == 2
    assert len({s["name"] for s in stores}) == len(stores)
    # every store and drop point is placed on the plan (x, z, facing and handover point)
    assert all({"x", "z", "fx", "fz", "front"} <= set(p) for p in stores + gates)
    h = {"X-API-Key": world.api_key}
    assert len(client.get("/v1/stores?zone=campus", headers=h).json()) == 28
    partners = client.get("/v1/partners?status=IDLE", headers=h).json()
    assert len(partners) == 12 and all(p["zone"] == "campus" for p in partners)


def test_ui_is_served_with_strict_csp(studio) -> None:  # noqa: ANN001
    client, _ = studio
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307 and root.headers["location"] == "/app/"
    page = client.get("/app/")
    assert page.status_code == 200 and "FlexDrop Studio" in page.text
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp and "tile.openstreetmap.org" in csp
    for asset in ("js/main.js", "js/engine.js", "css/app.css", "vendor/leaflet/leaflet.js"):
        assert client.get(f"/app/{asset}").status_code == 200, asset


def test_reports_endpoint(studio) -> None:  # noqa: ANN001
    client, world = studio
    data = client.get("/v1/reports/simulation", headers={"X-API-Key": world.api_key}).json()
    assert data["available"] and data["scenarios"]["campus"]["independent"]["fulfilment_cost_per_order"] == 80.0
    assert data["robustness"]["campus"]["flexdrop"]["cost_change_pct_mean"] == -15.0


def test_session_absent_outside_studio(client) -> None:  # noqa: ANN001
    assert client.get("/app/session").status_code == 404
    assert client.get("/app/").status_code == 200  # UI still served; it asks for an API key


def test_list_endpoints_filters_and_validation(client) -> None:  # noqa: ANN001
    from tests.integration.test_api import create_orders, now, optimize, setup_world

    setup_world(client, n_partners=2)
    placed = now()
    create_orders(client, 3, placed)
    orders = client.get("/v1/orders?status=PENDING", headers=headers("dispatch")).json()
    assert {o["id"] for o in orders} == {"O0", "O1", "O2"} and all(o["offers"] == [] for o in orders)
    optimize(client, placed + timedelta(minutes=8))
    plans = client.get("/v1/plans?status=PROPOSED", headers=headers("dispatch")).json()
    assert plans and all(p["status"] == "PROPOSED" for p in plans)
    assert client.get("/v1/plans?zone=campus", headers=headers("dispatch")).json() == plans
    assert client.get("/v1/plans?zone=elsewhere", headers=headers("dispatch")).json() == []
    assert client.get("/v1/orders?status=BOGUS", headers=headers("dispatch")).status_code == 422
    assert client.get("/v1/orders?zone=bad zone", headers=headers("dispatch")).status_code == 422
    assert client.get("/v1/orders", headers=headers("checkout")).status_code == 403
    assert client.get("/v1/stores", headers=headers("checkout")).status_code == 200
    assert client.get("/v1/reports/simulation", headers=headers("analyst")).json()["available"] in (True, False)


def test_reports_unavailable_when_missing(tmp_path) -> None:  # noqa: ANN001
    app = create_app(api_settings(tmp_path, reports_dir=str(tmp_path / "nothing")))
    with TestClient(app) as c:
        data = c.get("/v1/reports/simulation", headers=headers("analyst")).json()
        assert data == {"available": False, "generated_at": None, "scenarios": {}, "robustness": None}


def test_resolve_start() -> None:
    afternoon = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)  # 14:30 IST
    assert resolve_start("auto", afternoon) == afternoon
    assert resolve_start("now", afternoon) == afternoon
    late = datetime(2026, 11, 9, 18, 30, tzinfo=UTC)  # 00:00 IST next day
    start = resolve_start("auto", late).astimezone(IST)
    assert (start.hour, start.minute) == (19, 30) and start < late
    custom = resolve_start("12:15", afternoon).astimezone(IST)
    assert (custom.hour, custom.minute) == (12, 15)
    future = resolve_start("20:00", afternoon)
    assert future < afternoon and afternoon - future < timedelta(days=1)
    with pytest.raises(ValueError):
        resolve_start("lunch", afternoon)


def test_studio_refuses_production(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("FLEXDROP_ENV", "production")
    assert main(["studio", "--port", "1"]) == 2


def test_colab_launcher_outside_colab(tmp_path, capsys) -> None:  # noqa: ANN001
    import socket
    import urllib.request

    from flexdrop import colab

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = colab.launch(port=port, models=str(tmp_path / "no-models"), reports=str(tmp_path), partners=5)
    try:
        assert url == f"http://127.0.0.1:{port}/app/"
        assert "running at" in capsys.readouterr().out
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/app/session", timeout=10) as r:  # noqa: S310
            assert json.loads(r.read())["mode"] == "studio"
        assert colab.launch(port=port) == url  # second call re-uses the running server
    finally:
        colab.stop(port)


def test_website_has_no_em_or_en_dashes() -> None:
    """Style rule for the UI: no em dashes, en dashes or Unicode minus signs in website files."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "src" / "flexdrop" / "web"
    offenders = []
    for path in web.rglob("*"):
        if "vendor" in path.parts or not path.is_file() or path.suffix not in {".html", ".js", ".css"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(ch in line for ch in ("—", "–", "−")):
                offenders.append(f"{path.relative_to(web)}:{lineno}")
    assert offenders == []

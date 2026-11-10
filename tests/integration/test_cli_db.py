from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from flexdrop.api.security import hash_key
from flexdrop.cli import build_parser, main
from flexdrop.db.base import UTCDateTime
from flexdrop.db.models import AuditLogRow, OrderRow, StoreRow
from flexdrop.db.session import Database
from flexdrop.services.privacy import purge

ROOT = Path(__file__).resolve().parents[2]


def test_cli_create_api_key(capsys) -> None:  # noqa: ANN001
    assert main(["create-api-key", "--name", "checkout-svc", "--roles", "checkout"]) == 0
    out = capsys.readouterr().out.splitlines()
    key = out[1].strip()
    entry = json.loads(out[3])
    assert key.startswith("fdk_") and hash_key(key) in entry
    assert entry[hash_key(key)] == {"name": "checkout-svc", "roles": ["checkout"]}
    assert main(["create-api-key", "--name", "x", "--roles", "root"]) == 2


def test_cli_init_db_generate_and_train(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    url = f"sqlite:///{tmp_path}/cli.db"
    assert main(["init-db", "--database-url", url]) == 0
    db = Database(url)
    assert "orders" in inspect(db.engine).get_table_names()
    db.dispose()
    assert main(["generate-data", "--out", str(tmp_path / "d"), "--duration", "20", "--samples", "1500"]) == 0
    assert (tmp_path / "d" / "campus_orders.csv").exists() and (tmp_path / "d" / "historical_prep.csv").exists()
    assert main(["train", "--data", str(tmp_path / "d"), "--models", str(tmp_path / "m")]) == 0
    assert (tmp_path / "m" / "metadata.json").exists()
    monkeypatch.setenv("FLEXDROP_DATABASE_URL", url)
    from flexdrop.config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["kill-switch", "on", "--reason", "drill"]) == 0
        assert main(["purge", "--days", "30"]) == 0
    finally:
        get_settings.cache_clear()


def test_cli_parser_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["unknown"])


def test_cli_simulate(tmp_path, capsys) -> None:  # noqa: ANN001
    rc = main(
        [
            "simulate",
            "--duration",
            "15",
            "--policies",
            "independent,flexdrop",
            "--models",
            str(tmp_path / "none"),
            "--out",
            str(tmp_path / "r"),
        ]
    )
    assert rc == 0 and "Scenario: `campus`" in capsys.readouterr().out


def test_purge_removes_only_old_terminal_data(tmp_path) -> None:  # noqa: ANN001
    db = Database(f"sqlite:///{tmp_path}/p.db")
    db.create_all()
    old = datetime.now(tz=UTC) - timedelta(days=200)
    with db.session() as s:
        s.add(StoreRow(id="S", name="s", category="FOOD", lat=0, lng=0))
        s.flush()
        for oid, status, placed in (
            ("old", "DELIVERED", old),
            ("new", "DELIVERED", datetime.now(tz=UTC)),
            ("live", "PENDING", old),
        ):
            s.add(
                OrderRow(
                    id=oid,
                    store_id="S",
                    customer_ref="x" * 64,
                    category="FOOD",
                    pickup_lat=0,
                    pickup_lng=0,
                    drop_lat=0,
                    drop_lng=0,
                    placed_at=placed,
                    predicted_ready_at=placed,
                    ready_p90_at=placed,
                    promised_at=placed,
                    handling={"temp_classes": ["HOT"], "volume_l": 1, "weight_kg": 1},
                    status=status,
                )
            )
        s.add(AuditLogRow(actor="a", action="x", entity_type="t", entity_id="1", details={}, at=old))
    counts = purge(db, 90)
    assert counts["orders"] == 1 and counts["audit_logs"] == 1
    with db.session() as s:
        assert {o.id for o in s.query(OrderRow)} == {"new", "live"}
    with pytest.raises(ValueError):
        purge(db, 0)
    db.dispose()


def test_utc_datetime_rejects_naive() -> None:
    with pytest.raises(ValueError):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)  # type: ignore[arg-type]


def test_alembic_upgrade_matches_models(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    url = f"sqlite:///{tmp_path}/alembic.db"
    monkeypatch.setenv("FLEXDROP_DATABASE_URL", url)
    from flexdrop.config import get_settings

    get_settings.cache_clear()
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(cfg, "head")
        db = Database(url)
        tables = set(inspect(db.engine).get_table_names())
        assert {"orders", "plans", "plan_stops", "customer_consents", "audit_logs", "idempotency_records"} <= tables
        command.check(cfg)  # raises if ORM models drifted from migrations
        command.downgrade(cfg, "base")
        db.dispose()
        db = Database(url)
        assert set(inspect(db.engine).get_table_names()) <= {"alembic_version"}
        db.dispose()
    finally:
        get_settings.cache_clear()


def test_end_to_end_demo(tmp_path, capsys) -> None:  # noqa: ANN001
    from flexdrop.demo import run_demo

    result = run_demo(f"sqlite:///{tmp_path}/demo.db", str(tmp_path / "no-models"), n_orders=20)
    m = result["metrics"]
    assert m["orders_total"] == 20
    assert m["orders_delivered"] >= 15
    assert any("CRE action" in line for line in result["log"])
    assert (
        main(
            [
                "demo",
                "--database-url",
                f"sqlite:///{tmp_path}/demo2.db",
                "--models",
                str(tmp_path / "x"),
                "--orders",
                "8",
            ]
        )
        == 0
    )
    assert "registered" in capsys.readouterr().out


def test_demo_is_rerunnable_with_temporary_database(tmp_path) -> None:  # noqa: ANN001
    from flexdrop.demo import run_demo

    first = run_demo(None, str(tmp_path / "no-models"), n_orders=6)
    second = run_demo(None, str(tmp_path / "no-models"), n_orders=6)
    assert first["metrics"]["orders_total"] == second["metrics"]["orders_total"] == 6

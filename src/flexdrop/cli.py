"""Command-line interface: ``flexdrop <command>`` (see ``flexdrop --help``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flexdrop.logging_config import configure_logging


def _cmd_init_db(args: argparse.Namespace) -> int:
    from flexdrop.config import get_settings
    from flexdrop.db.session import Database

    url = args.database_url or get_settings().database_url
    db = Database(url)
    try:
        db.create_all()
    finally:
        db.dispose()
    print(f"schema created at {url}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    from flexdrop.simulation.export import export_scenario
    from flexdrop.simulation.history import generate_history
    from flexdrop.simulation.scenario import SCENARIOS, build_scenario

    sc = build_scenario(SCENARIOS[args.scenario](seed=args.seed, duration_min=args.duration))
    paths = export_scenario(sc, args.out)
    paths.update(
        generate_history(sc, args.out, n_prep=args.samples, n_trips=args.samples, n_offers=args.samples, seed=args.seed)
    )
    for k, v in paths.items():
        print(f"{k:10s} {v}")
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    from flexdrop.ml.training import train_all

    metrics = train_all(args.data, args.models, seed=args.seed)
    print(json.dumps(metrics, indent=2))
    return 0


def _cmd_simulate(args: argparse.Namespace) -> int:
    from flexdrop.simulation.policies import POLICIES
    from flexdrop.simulation.runner import render_markdown, run_policies
    from flexdrop.simulation.scenario import SCENARIOS, build_scenario

    policies = list(POLICIES) if args.policies == "all" else args.policies.split(",")
    sc = build_scenario(SCENARIOS[args.scenario](seed=args.seed, duration_min=args.duration))
    results = run_policies(sc, policies, args.models, args.out, seed=args.sim_seed, time_limit=args.time_limit)
    print(render_markdown(args.scenario, results))
    return 0


def _cmd_pipeline(args: argparse.Namespace) -> int:
    from flexdrop.simulation.policies import POLICIES
    from flexdrop.simulation.runner import full_pipeline

    policies = list(POLICIES) if args.policies == "all" else args.policies.split(",")
    full_pipeline(
        args.data,
        args.models,
        args.reports,
        scenarios=args.scenarios.split(","),
        policies=policies,
        duration_min=args.duration,
        seed=args.seed,
        time_limit=args.time_limit,
    )
    print((Path(args.reports) / "SUMMARY.md").read_text())
    return 0


def _cmd_robustness(args: argparse.Namespace) -> int:
    from flexdrop.simulation.runner import seed_robustness

    seed_robustness(
        args.models,
        args.reports,
        scenarios=args.scenarios.split(","),
        policies=args.policies.split(","),
        seeds=[int(x) for x in args.seeds.split(",")],
        duration_min=args.duration,
        time_limit=args.time_limit,
    )
    print((Path(args.reports) / "ROBUSTNESS.md").read_text())
    return 0


def _cmd_studio(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    from flexdrop.studio import build_studio_app

    if os.environ.get("FLEXDROP_ENV", "").lower() == "production":
        print("studio is a demo mode and refuses to run with FLEXDROP_ENV=production", file=sys.stderr)
        return 2
    app, world, cleanup = build_studio_app(
        model_dir=args.models,
        database_url=args.database_url,
        reports_dir=args.reports,
        n_partners=args.partners,
        start=args.start,
    )
    url = f"http://{args.host}:{args.port}/app/"
    print(f"FlexDrop Studio: open {url}")
    print(f"(session API key for scripts: {world.api_key})")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        cleanup()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "flexdrop.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        proxy_headers=True,
        log_config=None,
    )
    return 0


def _cmd_create_key(args: argparse.Namespace) -> int:
    from flexdrop.api.security import ROLES, generate_key, hash_key

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    bad = set(roles) - ROLES
    if bad:
        print(f"unknown roles: {sorted(bad)}; valid: {sorted(ROLES)}", file=sys.stderr)
        return 2
    key = generate_key()
    print("API key (shown once, store it in your secret manager):")
    print(f"  {key}")
    print("Add this entry to FLEXDROP_API_KEYS:")
    print(json.dumps({hash_key(key): {"name": args.name, "roles": roles}}))
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    from flexdrop.config import get_settings
    from flexdrop.db.session import Database
    from flexdrop.services.privacy import purge

    settings = get_settings()
    days = args.days or settings.data_retention_days
    db = Database(settings.database_url)
    try:
        print(json.dumps(purge(db, days), indent=2))
    finally:
        db.dispose()
    return 0


def _cmd_kill_switch(args: argparse.Namespace) -> int:
    from flexdrop.config import get_settings
    from flexdrop.db.session import Database
    from flexdrop.services.common import set_kill_switch

    settings = get_settings()
    db = Database(settings.database_url)
    try:
        with db.session() as s:
            set_kill_switch(s, args.state == "on", args.reason, "cli")
    finally:
        db.dispose()
    print(f"kill switch {args.state}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from flexdrop.demo import run_demo

    try:
        result = run_demo(args.database_url, args.models, n_orders=args.orders)
    except RuntimeError as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    for line in result["log"]:
        print("-", line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flexdrop", description="FlexDrop adaptive delivery coordination engine")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-db", help="create database schema (dev; production uses alembic)")
    s.add_argument("--database-url", default=None)
    s.set_defaults(func=_cmd_init_db)

    s = sub.add_parser("generate-data", help="generate a synthetic scenario and historical training data")
    s.add_argument("--scenario", choices=["campus", "city"], default="campus")
    s.add_argument("--out", default="data/sample")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--duration", type=float, default=240.0)
    s.add_argument("--samples", type=int, default=20000)
    s.set_defaults(func=_cmd_generate)

    s = sub.add_parser("train", help="train prediction models from historical CSVs")
    s.add_argument("--data", default="data/sample")
    s.add_argument("--models", default="models")
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(func=_cmd_train)

    s = sub.add_parser("simulate", help="compare dispatch policies on a scenario")
    s.add_argument("--scenario", choices=["campus", "city"], default="campus")
    s.add_argument("--policies", default="all")
    s.add_argument("--models", default="models")
    s.add_argument("--out", default="reports")
    s.add_argument("--duration", type=float, default=240.0)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--sim-seed", type=int, default=1)
    s.add_argument("--time-limit", type=float, default=1.0)
    s.set_defaults(func=_cmd_simulate)

    s = sub.add_parser("pipeline", help="generate -> train -> calibrate -> simulate all policies")
    s.add_argument("--data", default="data/sample")
    s.add_argument("--models", default="models")
    s.add_argument("--reports", default="reports")
    s.add_argument("--scenarios", default="campus,city")
    s.add_argument("--policies", default="all")
    s.add_argument("--duration", type=float, default=240.0)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--time-limit", type=float, default=1.0)
    s.set_defaults(func=_cmd_pipeline)

    s = sub.add_parser("robustness", help="repeat the policy comparison over several scenario seeds")
    s.add_argument("--models", default="models")
    s.add_argument("--reports", default="reports")
    s.add_argument("--scenarios", default="campus,city")
    s.add_argument("--policies", default="independent,batching,flexdrop")
    s.add_argument("--seeds", default="7,8,9,10,11")
    s.add_argument("--duration", type=float, default=240.0)
    s.add_argument("--time-limit", type=float, default=1.0)
    s.set_defaults(func=_cmd_robustness)

    s = sub.add_parser("studio", help="browser prototype: seeded campus world + UI at /app/")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--models", default="models")
    s.add_argument("--reports", default="reports")
    s.add_argument("--partners", type=int, default=40)
    s.add_argument("--database-url", default=None, help="default: a fresh temporary SQLite database")
    s.add_argument("--start", default="auto", help="simulated start: auto (default), now, or HH:MM IST")
    s.set_defaults(func=_cmd_studio)

    s = sub.add_parser("serve", help="run the HTTP API (uvicorn)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1)
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser("create-api-key", help="generate an API key and its configuration entry")
    s.add_argument("--name", required=True)
    s.add_argument("--roles", required=True, help="comma-separated: checkout,dispatch,analyst,admin")
    s.set_defaults(func=_cmd_create_key)

    s = sub.add_parser("purge", help="delete data older than the retention period")
    s.add_argument("--days", type=int, default=None)
    s.set_defaults(func=_cmd_purge)

    s = sub.add_parser("kill-switch", help="enable/disable FlexDrop assignments")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("--reason", required=True)
    s.set_defaults(func=_cmd_kill_switch)

    s = sub.add_parser("demo", help="scripted end-to-end API demonstration")
    s.add_argument("--database-url", default=None, help="default: a fresh temporary SQLite database")
    s.add_argument("--models", default="models")
    s.add_argument("--orders", type=int, default=24)
    s.set_defaults(func=_cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level.upper(), json_output=False)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

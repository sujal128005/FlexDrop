"""End-to-end experiment runner: data generation, training, calibration and policy comparison."""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

from flexdrop.config import TravelParams
from flexdrop.ml.registry import load_bundle
from flexdrop.ml.training import train_all, write_coordination_samples
from flexdrop.simulation.export import export_scenario
from flexdrop.simulation.history import generate_history
from flexdrop.simulation.policies import POLICIES, get_policy
from flexdrop.simulation.scenario import SCENARIOS, Scenario, build_scenario
from flexdrop.simulation.simulator import SimulationResult, Simulator

log = logging.getLogger(__name__)

REPORT_METRICS: list[tuple[str, str, str]] = [
    # key, label, better ("lower" | "higher" | "")
    ("orders_delivered", "Orders delivered", ""),
    ("fulfilment_cost_per_order", "Fulfilment cost / order incl. rewards (INR)", "lower"),
    ("delivery_cost_per_order", "Delivery cost / order excl. rewards (INR)", "lower"),
    ("rewards_per_order", "Rewards / order (INR)", ""),
    ("km_per_order", "Distance / order (km)", "lower"),
    ("avg_orders_per_route", "Orders per route", "higher"),
    ("coordinated_share", "Coordinated share of orders", "higher"),
    ("on_time_rate", "On-time rate (within approved window)", "higher"),
    ("standard_promise_met_rate", "Delivered by standard promise", ""),
    ("avg_click_to_door_min", "Avg click-to-door (min)", "lower"),
    ("p90_click_to_door_min", "P90 click-to-door (min)", "lower"),
    ("food_quality_violations", "Food-quality violations", "lower"),
    ("partner_earnings_per_engaged_hour", "Partner earnings / engaged hour (INR)", ""),
    ("orders_per_partner_engaged_hour", "Orders / partner engaged hour", "higher"),
    ("avg_store_wait_min", "Avg partner wait at store (min)", "lower"),
    ("acceptance_rate", "Flexible-offer acceptance rate", ""),
    ("flex_share_of_orders", "Share of orders on flexible delivery", ""),
    ("flex_utilisation", "Flexibility used / approved", ""),
    ("fallback_routes", "Fallback (late-risk) routes", "lower"),
    ("reopt_events", "Re-optimisation events", ""),
    ("insertions", "Insertions into live routes", ""),
    ("optimization_latency_p95_ms", "Optimisation latency P95 (ms)", "lower"),
]


def write_orders_csv(result: SimulationResult, path: Path) -> None:
    if not result.orders:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(result.orders[0]))
        w.writeheader()
        w.writerows(result.orders)


def render_markdown(scenario: str, results: dict[str, dict[str, Any]], baseline: str = "independent") -> str:
    policies = list(results)
    lines = [f"### Scenario: `{scenario}`", ""]
    header = "| Metric | " + " | ".join(f"`{p}`" for p in policies) + " |"
    lines += [header, "|---|" + "---:|" * len(policies)]
    for key, label, _ in REPORT_METRICS:
        cells = []
        for p in policies:
            v = results[p].get(key, "")
            cells.append(f"{v:.4g}" if isinstance(v, float) else str(v))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if baseline in results:
        base = results[baseline]
        lines += ["", f"Relative change vs `{baseline}`:", ""]
        lines += [
            "| Policy | Cost / order | Distance / order | On-time rate (pp) | Avg click-to-door |",
            "|---|---:|---:|---:|---:|",
        ]
        for p in policies:
            if p == baseline:
                continue
            r = results[p]

            def rel(k: str, r: dict[str, Any] = r) -> str:
                b = base[k]
                return f"{(r[k] - b) / b * 100:+.1f}%" if b else "n/a"

            lines.append(
                f"| `{p}` | {rel('fulfilment_cost_per_order')} | {rel('km_per_order')} | "
                f"{(r['on_time_rate'] - base['on_time_rate']) * 100:+.1f} | {rel('avg_click_to_door_min')} |"
            )
    return "\n".join(lines) + "\n"


def run_policies(
    scenario: Scenario,
    policies: list[str],
    model_dir: str | Path,
    out_dir: str | Path,
    seed: int = 1,
    time_limit: float = 1.0,
) -> dict[str, dict[str, Any]]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    models = load_bundle(model_dir, TravelParams())
    results: dict[str, dict[str, Any]] = {}
    for name in policies:
        spec = get_policy(name, time_limit)
        started = time.perf_counter()
        result = Simulator(scenario, spec, models, seed=seed).run()
        result.metrics["wall_clock_s"] = round(time.perf_counter() - started, 2)
        results[name] = result.metrics
        write_orders_csv(result, out / f"{scenario.spec.name}_{name}_orders.csv")
        log.info("simulated %s/%s in %.1fs", scenario.spec.name, name, result.metrics["wall_clock_s"])
    (out / f"{scenario.spec.name}_metrics.json").write_text(json.dumps(results, indent=2))
    (out / f"{scenario.spec.name}_comparison.md").write_text(render_markdown(scenario.spec.name, results))
    return results


def calibrate_coordination(
    scenario: Scenario,
    model_dir: str | Path,
    data_dir: str | Path,
    seeds: tuple[int, ...] = tuple(range(101, 109)),
    min_samples: int = 800,
) -> Path:
    """Run FlexDrop on independent seeds and log (features, coordinated) samples.

    Stops once ``min_samples`` samples are collected (or the seeds are exhausted).
    """
    models = load_bundle(model_dir, TravelParams())
    samples: list[dict[str, Any]] = []
    for s in seeds:
        result = Simulator(scenario, get_policy("flexdrop"), models, seed=s).run()
        samples.extend(result.coordination_samples)
        if len(samples) >= min_samples:
            break
    path = Path(data_dir) / "coordination_samples.csv"
    write_coordination_samples(samples, path)
    log.info("collected %d coordination samples", len(samples))
    return path


def full_pipeline(
    data_dir: str | Path,
    model_dir: str | Path,
    report_dir: str | Path,
    scenarios: list[str] | None = None,
    policies: list[str] | None = None,
    duration_min: float = 240.0,
    seed: int = 42,
    time_limit: float = 1.0,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Generate data -> train -> calibrate coordination model -> retrain -> compare policies."""
    scenarios = scenarios or ["campus", "city"]
    policies = policies or list(POLICIES)
    data = Path(data_dir)
    training_scenario = build_scenario(SCENARIOS["campus"](seed=seed + 1000, duration_min=duration_min))
    generate_history(training_scenario, data, seed=seed)
    coord = data / "coordination_samples.csv"
    if coord.exists():
        coord.unlink()
    train_all(data, model_dir, seed=seed)
    calibrate_coordination(training_scenario, model_dir, data)
    train_all(data, model_dir, seed=seed)

    all_results: dict[str, dict[str, dict[str, Any]]] = {}
    for name in scenarios:
        sc = build_scenario(SCENARIOS[name](seed=seed, duration_min=duration_min))
        export_scenario(sc, data)
        all_results[name] = run_policies(sc, policies, model_dir, report_dir, seed=1, time_limit=time_limit)
    summary = ["# FlexDrop simulation results", ""]
    summary.append("Generated by `flexdrop pipeline`. Synthetic data; costs are hypothetical modelling assumptions.\n")
    for name in scenarios:
        summary.append(render_markdown(name, all_results[name]))
    Path(report_dir, "SUMMARY.md").write_text("\n".join(summary))
    return all_results


def seed_robustness(
    model_dir: str | Path,
    report_dir: str | Path,
    scenarios: list[str],
    policies: list[str],
    seeds: list[int],
    duration_min: float = 240.0,
    time_limit: float = 1.0,
) -> dict[str, dict[str, dict[str, float]]]:
    """Repeat the comparison over independent scenario seeds; report mean and std of key KPIs."""
    import numpy as np

    keys = ("fulfilment_cost_per_order", "on_time_rate", "coordinated_share", "avg_click_to_door_min", "km_per_order")
    models = load_bundle(model_dir, TravelParams())
    summary: dict[str, dict[str, dict[str, float]]] = {}
    lines = ["# Seed robustness", "", f"Scenario seeds: {seeds}; duration {duration_min:.0f} min.", ""]
    for name in scenarios:
        per_policy: dict[str, dict[str, list[float]]] = {p: {k: [] for k in keys} for p in policies}
        rel_cost: dict[str, list[float]] = {p: [] for p in policies}
        rel_ontime: dict[str, list[float]] = {p: [] for p in policies}
        for seed in seeds:
            sc = build_scenario(SCENARIOS[name](seed=seed, duration_min=duration_min))
            run = {p: Simulator(sc, get_policy(p, time_limit), models, seed=1).run().metrics for p in policies}
            for p in policies:
                for k in keys:
                    per_policy[p][k].append(float(run[p][k]))
                if "independent" in run:
                    base = run["independent"]
                    rel_cost[p].append(
                        (run[p]["fulfilment_cost_per_order"] - base["fulfilment_cost_per_order"])
                        / base["fulfilment_cost_per_order"]
                        * 100
                    )
                    rel_ontime[p].append((run[p]["on_time_rate"] - base["on_time_rate"]) * 100)
        summary[name] = {
            p: {
                **{f"{k}_mean": float(np.mean(v)) for k, v in per_policy[p].items()},
                **{f"{k}_std": float(np.std(v)) for k, v in per_policy[p].items()},
                "cost_change_pct_mean": float(np.mean(rel_cost[p])) if rel_cost[p] else 0.0,
                "cost_change_pct_std": float(np.std(rel_cost[p])) if rel_cost[p] else 0.0,
                "on_time_change_pp_mean": float(np.mean(rel_ontime[p])) if rel_ontime[p] else 0.0,
                "on_time_change_pp_std": float(np.std(rel_ontime[p])) if rel_ontime[p] else 0.0,
            }
            for p in policies
        }
        lines += [
            f"## `{name}`",
            "",
            "| Policy | Cost / order (INR) | Cost vs independent | On-time rate | On-time vs independent (pp) "
            "| Coordinated share |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for p in policies:
            s = summary[name][p]
            lines.append(
                f"| `{p}` | {s['fulfilment_cost_per_order_mean']:.2f} ± {s['fulfilment_cost_per_order_std']:.2f} "
                f"| {s['cost_change_pct_mean']:+.1f}% ± {s['cost_change_pct_std']:.1f} "
                f"| {s['on_time_rate_mean']:.3f} ± {s['on_time_rate_std']:.3f} "
                f"| {s['on_time_change_pp_mean']:+.1f} ± {s['on_time_change_pp_std']:.1f} "
                f"| {s['coordinated_share_mean']:.3f} |"
            )
        lines.append("")
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "robustness.json").write_text(json.dumps(summary, indent=2))
    (out / "ROBUSTNESS.md").write_text("\n".join(lines) + "\n")
    return summary

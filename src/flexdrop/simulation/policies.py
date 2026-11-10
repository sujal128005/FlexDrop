"""Dispatch policies compared in the simulation.

All policies share the same engines and constraints so that differences come only from the
coordination strategy:

* ``independent``  - every order dispatched alone, first-come-first-served, nearest feasible
  partner (no batching, no flexibility).
* ``batching``     - conventional batching: pairs of same-category orders from the same
  store (<= 300 m), only when both standard promises hold; greedy selection; no flexibility.
* ``flexdrop_noflex`` - FlexDrop optimiser (groups <= 3, CP-SAT) *without* flexible
  offers - ablation isolating the value of customer flexibility.
* ``flexdrop``     - full FlexDrop (Level 1: same-category, groups <= 3, flexible offers,
  holds, CP-SAT).
* ``flexdrop_l3``  - FlexDrop Level 3 (cross-category, groups <= 4, insertion).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from flexdrop.config import EngineConfig


@dataclass(frozen=True)
class PolicySpec:
    name: str
    description: str
    engine: EngineConfig
    offers_enabled: bool


def _base(level: int, time_limit: float) -> EngineConfig:
    return EngineConfig.for_level(level, cpsat_time_limit_s=time_limit, cpsat_workers=1)


def independent_policy(time_limit: float = 1.0) -> PolicySpec:
    cfg = _base(1, time_limit)
    cfg = replace(
        cfg,
        optimizer=replace(cfg.optimizer, max_group_size=1, hold_enabled=False, insertion_enabled=False, solver="fifo"),
    )
    return PolicySpec("independent", "Independent dispatch (FIFO, nearest feasible partner)", cfg, False)


def batching_policy(time_limit: float = 1.0) -> PolicySpec:
    cfg = _base(1, time_limit)
    cfg = replace(
        cfg,
        compat=replace(cfg.compat, same_store_only=True, cross_category_enabled=False, max_drop_distance_km=1.5),
        optimizer=replace(
            cfg.optimizer, max_group_size=2, hold_enabled=False, insertion_enabled=False, solver="greedy"
        ),
    )
    return PolicySpec("batching", "Conventional same-store batching (greedy, no flexibility)", cfg, False)


def flexdrop_noflex_policy(time_limit: float = 1.0) -> PolicySpec:
    cfg = _base(1, time_limit)
    return PolicySpec("flexdrop_noflex", "FlexDrop optimiser without flexible offers (ablation)", cfg, False)


def flexdrop_policy(time_limit: float = 1.0) -> PolicySpec:
    return PolicySpec(
        "flexdrop", "FlexDrop Level 1 (same-category, flexible offers, CP-SAT)", _base(1, time_limit), True
    )


def flexdrop_l3_policy(time_limit: float = 1.0) -> PolicySpec:
    return PolicySpec(
        "flexdrop_l3", "FlexDrop Level 3 (cross-category, groups<=4, insertion)", _base(3, time_limit), True
    )


POLICIES = {
    "independent": independent_policy,
    "batching": batching_policy,
    "flexdrop_noflex": flexdrop_noflex_policy,
    "flexdrop": flexdrop_policy,
    "flexdrop_l3": flexdrop_l3_policy,
}


def get_policy(name: str, time_limit: float = 1.0) -> PolicySpec:
    try:
        return POLICIES[name](time_limit)
    except KeyError as exc:
        raise ValueError(f"unknown policy '{name}'; choose from {sorted(POLICIES)}") from exc

"""scenarios.py — evaluation scenarios for the remediation harness.

Each scenario builds a FRESH device in a known faulted state and declares the
ground-truth fault. There is no agent or brain here; scenarios only describe
faulted worlds for something else to diagnose and fix.

Parameters are kept moderate so the single canonical remediation is sufficient
to restore health. In particular, thermal scenarios stay at batch_size <= 40 so
switching to power_mode "eco" alone clears the overheat (above ~40 the residual
batch heat keeps the device hot even in eco).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fleet import Device, Params


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Callable[[], Device]  # returns a fresh faulted Device each call
    fault: str  # ground truth: "thermal" | "latency" | "error"


def _faulted(
    name: str,
    fault: str,
    *,
    batch_size: int,
    worker_count: int,
    power_mode: str,
    site: str = "plant-eval",
) -> Device:
    """Build a fresh device with the given starting params and inject the fault."""
    device = Device(
        id=name,
        site=site,
        params=Params(
            batch_size=batch_size,
            worker_count=worker_count,
            power_mode=power_mode,
        ),
    )
    device.inject_fault(fault)
    return device


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
#
# Starting params vary within safe ranges; each remains solvable by its single
# canonical fix (thermal->eco, latency->workers>=4, error->restart_worker).

SCENARIOS = [
    # --- thermal: fixed by power_mode "eco" (batch_size kept <= 40) ---------
    Scenario(
        name="thermal_basic",
        fault="thermal",
        build=lambda: _faulted(
            "thermal_basic", "thermal",
            batch_size=8, worker_count=2, power_mode="normal",
        ),
    ),
    Scenario(
        name="thermal_turbo_load",
        fault="thermal",
        build=lambda: _faulted(
            "thermal_turbo_load", "thermal",
            batch_size=16, worker_count=4, power_mode="turbo",
        ),
    ),
    # --- latency: fixed by scaling workers to >= 4 (start with < 4) ---------
    Scenario(
        name="latency_basic",
        fault="latency",
        build=lambda: _faulted(
            "latency_basic", "latency",
            batch_size=8, worker_count=2, power_mode="normal",
        ),
    ),
    Scenario(
        name="latency_heavy_batch",
        fault="latency",
        build=lambda: _faulted(
            "latency_heavy_batch", "latency",
            batch_size=32, worker_count=3, power_mode="eco",
        ),
    ),
    # --- error: fixed by restart_worker (params don't affect error_rate) ----
    Scenario(
        name="error_basic",
        fault="error",
        build=lambda: _faulted(
            "error_basic", "error",
            batch_size=8, worker_count=2, power_mode="normal",
        ),
    ),
    Scenario(
        name="error_busy",
        fault="error",
        build=lambda: _faulted(
            "error_busy", "error",
            batch_size=16, worker_count=6, power_mode="turbo",
        ),
    ),
]


# --------------------------------------------------------------------------- #
# Self-check (fleet-level only; no agent/brain)
# --------------------------------------------------------------------------- #


def _self_check() -> None:
    """Confirm every scenario starts unhealthy and is solved by its canonical
    remediation. Uses only fleet.py — no agent or brain involved."""
    from fleet import remediation_for

    for s in SCENARIOS:
        device = s.build()
        assert not device.is_healthy(), f"{s.name} should start unhealthy"
        device.apply(remediation_for(s.fault))
        assert device.is_healthy(), f"{s.name} not solved by canonical fix"
        print(f"OK  {s.name:20} fault={s.fault}")

    print(f"\n{len(SCENARIOS)} scenarios, all start unhealthy and are solvable.")


if __name__ == "__main__":
    _self_check()

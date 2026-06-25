"""fleet.py — a deterministic simulator of a fleet of edge AI inference nodes.

This is just the "world": the devices the agent will later observe and act on.
There is no LLM and no agent here.

Each device carries three SEPARATE kinds of state:

  1. Controllable parameters — the agent's action surface:
        batch_size   : int in 1..64
        worker_count : int in 1..8
        power_mode   : "eco" | "normal" | "turbo"

  2. Observable telemetry — computed, read-only, a pure function of
     (parameters + fault):
        cpu_temp_c, inference_latency_ms, error_rate

  3. A hidden fault — None | "thermal" | "latency" | "error".

A device is healthy iff every telemetry value is within its threshold. With
default parameters and no fault, a device is healthy. Each fault, when active,
pushes exactly its own metric past the threshold, and the matching remediation
brings the device back to healthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- #
# Parameter ranges and health thresholds
# --------------------------------------------------------------------------- #

BATCH_SIZE_RANGE = (1, 64)
WORKER_COUNT_RANGE = (1, 8)
POWER_MODES = ("eco", "normal", "turbo")

TEMP_THRESHOLD_C = 80.0
LATENCY_THRESHOLD_MS = 250.0
ERROR_RATE_THRESHOLD = 0.05

FAULTS = (None, "thermal", "latency", "error")


# --------------------------------------------------------------------------- #
# Telemetry model — pure functions of (parameters + fault)
# --------------------------------------------------------------------------- #
#
# The constants are chosen so that:
#   * default params + no fault  -> comfortably healthy on all three metrics
#   * each fault                 -> pushes exactly its own metric over threshold
#   * the matching remediation   -> pulls that metric back under threshold while
#                                   the fault is still present (thermal, latency),
#                                   or clears the fault outright (error / restart)

# Temperature: base + power-mode offset + a little heat per unit of batch.
TEMP_BASE_C = 50.0
TEMP_BY_POWER_MODE = {"eco": -25.0, "normal": 0.0, "turbo": 18.0}
TEMP_PER_BATCH_C = 0.5
THERMAL_FAULT_C = 35.0  # added when the thermal fault is active

# Latency: base + cost per unit of batch + a fault backlog cleared by workers.
LATENCY_BASE_MS = 60.0
LATENCY_PER_BATCH_MS = 2.0
LATENCY_FAULT_BACKLOG_MS = 400.0  # divided across worker_count when fault active

# Error rate: a low floor, lifted hard while the error fault is active.
ERROR_RATE_BASE = 0.01
ERROR_FAULT_RATE = 0.20


def compute_cpu_temp_c(params: "Params", fault: Optional[str]) -> float:
    temp = (
        TEMP_BASE_C
        + TEMP_BY_POWER_MODE[params.power_mode]
        + params.batch_size * TEMP_PER_BATCH_C
    )
    if fault == "thermal":
        temp += THERMAL_FAULT_C
    return round(temp, 1)


def compute_inference_latency_ms(params: "Params", fault: Optional[str]) -> float:
    latency = LATENCY_BASE_MS + params.batch_size * LATENCY_PER_BATCH_MS
    if fault == "latency":
        # A backlog the workers must drain: more workers clears it faster.
        latency += LATENCY_FAULT_BACKLOG_MS / params.worker_count
    return round(latency, 1)


def compute_error_rate(params: "Params", fault: Optional[str]) -> float:
    rate = ERROR_RATE_BASE
    if fault == "error":
        rate += ERROR_FAULT_RATE
    return round(rate, 4)


# --------------------------------------------------------------------------- #
# Controllable parameters
# --------------------------------------------------------------------------- #


@dataclass
class Params:
    """The agent's action surface. Validated on construction."""

    batch_size: int = 8
    worker_count: int = 2
    power_mode: str = "normal"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        lo, hi = BATCH_SIZE_RANGE
        if not (lo <= self.batch_size <= hi):
            raise ValueError(f"batch_size {self.batch_size} out of range {lo}..{hi}")
        lo, hi = WORKER_COUNT_RANGE
        if not (lo <= self.worker_count <= hi):
            raise ValueError(f"worker_count {self.worker_count} out of range {lo}..{hi}")
        if self.power_mode not in POWER_MODES:
            raise ValueError(f"power_mode {self.power_mode!r} not in {POWER_MODES}")


# --------------------------------------------------------------------------- #
# Telemetry (read-only view)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Telemetry:
    cpu_temp_c: float
    inference_latency_ms: float
    error_rate: float

    @property
    def healthy(self) -> bool:
        return (
            self.cpu_temp_c <= TEMP_THRESHOLD_C
            and self.inference_latency_ms <= LATENCY_THRESHOLD_MS
            and self.error_rate <= ERROR_RATE_THRESHOLD
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "cpu_temp_c": self.cpu_temp_c,
            "inference_latency_ms": self.inference_latency_ms,
            "error_rate": self.error_rate,
        }


# --------------------------------------------------------------------------- #
# Device
# --------------------------------------------------------------------------- #


@dataclass
class Device:
    id: str
    site: str
    site_type: str = "industrial"
    params: Params = field(default_factory=Params)
    fault: Optional[str] = None  # hidden ground-truth state

    # ---- telemetry -------------------------------------------------------- #

    def telemetry(self) -> Telemetry:
        """Compute current telemetry. Pure function of (params + fault)."""
        return Telemetry(
            cpu_temp_c=compute_cpu_temp_c(self.params, self.fault),
            inference_latency_ms=compute_inference_latency_ms(self.params, self.fault),
            error_rate=compute_error_rate(self.params, self.fault),
        )

    def is_healthy(self) -> bool:
        return self.telemetry().healthy

    # ---- fault injection (for building scenarios) ------------------------- #

    def inject_fault(self, fault: Optional[str]) -> None:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}, expected one of {FAULTS}")
        self.fault = fault

    # ---- actions (the agent's effect on the world) ------------------------ #

    def set_param(self, name: str, value) -> None:
        """Change one controllable parameter. Validated; never touches faults."""
        if name not in ("batch_size", "worker_count", "power_mode"):
            raise ValueError(f"{name!r} is not a controllable parameter")
        self.params = replace(self.params, **{name: value})  # re-validates

    def restart_worker(self) -> None:
        """Restart a worker. Clears a transient error fault; otherwise a no-op
        on the fault state (parameters are untouched)."""
        if self.fault == "error":
            self.fault = None

    def apply(self, action: Dict) -> None:
        """Apply a structured action and let the world respond.

        Action shapes:
            {"type": "set_param", "param": <name>, "value": <value>}
            {"type": "restart_worker"}
        """
        kind = action.get("type")
        if kind == "set_param":
            self.set_param(action["param"], action["value"])
        elif kind == "restart_worker":
            self.restart_worker()
        else:
            raise ValueError(f"unknown action type {kind!r}")

    # ---- snapshot / restore (for rollback) -------------------------------- #

    def snapshot(self) -> Dict:
        """Capture full device state so it can be restored later."""
        return {
            "id": self.id,
            "site": self.site,
            "site_type": self.site_type,
            "batch_size": self.params.batch_size,
            "worker_count": self.params.worker_count,
            "power_mode": self.params.power_mode,
            "fault": self.fault,
        }

    def restore(self, snap: Dict) -> None:
        """Restore full device state from a snapshot()."""
        self.id = snap["id"]
        self.site = snap["site"]
        self.site_type = snap["site_type"]
        self.params = Params(
            batch_size=snap["batch_size"],
            worker_count=snap["worker_count"],
            power_mode=snap["power_mode"],
        )
        self.fault = snap["fault"]

    # ---- observable view (telemetry only; fault stays hidden) ------------- #

    def view(self) -> Dict:
        t = self.telemetry()
        return {
            "id": self.id,
            "site": self.site,
            "site_type": self.site_type,
            "params": {
                "batch_size": self.params.batch_size,
                "worker_count": self.params.worker_count,
                "power_mode": self.params.power_mode,
            },
            "telemetry": t.as_dict(),
            "healthy": t.healthy,
        }


# --------------------------------------------------------------------------- #
# The correct remediation per fault
# --------------------------------------------------------------------------- #

REMEDIATION = {
    "thermal": {"type": "set_param", "param": "power_mode", "value": "eco"},
    "latency": {"type": "set_param", "param": "worker_count", "value": 4},
    "error": {"type": "restart_worker"},
}


def remediation_for(fault: Optional[str]) -> Optional[Dict]:
    """Return the action known to fix the given fault, or None."""
    return REMEDIATION.get(fault) if fault else None


# --------------------------------------------------------------------------- #
# Fleet
# --------------------------------------------------------------------------- #


class Fleet:
    """A container of devices keyed by id."""

    def __init__(self, devices: List[Device]) -> None:
        self._devices: Dict[str, Device] = {d.id: d for d in devices}

    def get(self, device_id: str) -> Device:
        return self._devices[device_id]

    def status(self) -> List[Dict]:
        """Observable view of every device."""
        return [d.view() for d in self._devices.values()]

    def unhealthy(self) -> List[str]:
        """Ids of all devices currently failing a health threshold."""
        return [d.id for d in self._devices.values() if not d.is_healthy()]


# --------------------------------------------------------------------------- #
# Default fleet
# --------------------------------------------------------------------------- #


def default_fleet() -> Fleet:
    """A small healthy fleet of industrial edge nodes."""
    return Fleet(
        [
            Device(id="edge-01", site="plant-north"),
            Device(id="edge-02", site="plant-north"),
            Device(id="edge-03", site="plant-south"),
            Device(id="edge-04", site="warehouse-east"),
        ]
    )


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


def _print_fleet(fleet: Fleet, title: str) -> None:
    print(f"\n=== {title} ===")
    for v in fleet.status():
        t = v["telemetry"]
        flag = "OK " if v["healthy"] else "BAD"
        print(
            f"[{flag}] {v['id']} @ {v['site']} ({v['site_type']}) "
            f"params={v['params']} "
            f"temp={t['cpu_temp_c']}C "
            f"latency={t['inference_latency_ms']}ms "
            f"err={t['error_rate']}"
        )
    print(f"unhealthy: {fleet.unhealthy()}")


def main() -> None:
    fleet = default_fleet()
    _print_fleet(fleet, "initial fleet (all healthy)")

    # Inject a thermal fault into one device.
    target = fleet.get("edge-02")
    target.inject_fault("thermal")
    _print_fleet(fleet, "after injecting a thermal fault on edge-02")

    # Apply the correct remediation and watch the world respond.
    fix = remediation_for(target.fault)
    print(f"\napplying remediation to edge-02: {fix}")
    target.apply(fix)
    _print_fleet(fleet, "after remediating edge-02")


if __name__ == "__main__":
    main()

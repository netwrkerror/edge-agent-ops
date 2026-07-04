"""serve_live.py — a live Prometheus target.

Runs continuous remediation sweeps over a fleet with staggered injected faults,
updates the metrics in metrics.py, and serves /metrics on :8000 so Prometheus
has something real to scrape. Uses the existing modules unchanged.

    python3 serve_live.py                 # mock brain (fast; default)
    python3 serve_live.py --backend ollama --model qwen3:8b

The injected behavior rotates so ALL remediation outcomes occur over time —
success, denied, rolled_back, declined — and fleet_healthy dips while a fault is
unresolved and recovers on the next cycle. Otherwise the denial/rollback series
would sit at zero and a dashboard would have nothing to show.
"""

from __future__ import annotations

import argparse
import itertools
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator

import agent
import evaluate
import guardrails
import metrics
from fleet import default_fleet
from guardrails import AuditLog


# --------------------------------------------------------------------------- #
# Scripted brains + approval override (to force specific outcomes)
# --------------------------------------------------------------------------- #


class _Stub:
    """A brain that always proposes one fixed action."""

    def __init__(self, action: str, value: Any, diagnosis: str = "scripted") -> None:
        self._proposal = {
            "action": action,
            "value": value,
            "diagnosis": diagnosis,
            "reason": "scripted live-metrics action",
        }

    def decide(self, view: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._proposal)


# out-of-bounds value -> policy DENY
_OUT_OF_BOUNDS = _Stub("scale_workers", 99, "thermal")
# in-bounds but useless for a thermal fault -> applied then verification fails -> rolled_back
_WRONG_FIX = _Stub("scale_workers", 8, "thermal")
# proposes the high-risk restart; paired with a declined approval below
_RESTART = _Stub("restart_worker", None, "error")
# a brain that can't diagnose a genuinely faulted device -> no_diagnosis (not a
# healthy device being swept, which would be a meaningless noop)
_NOOP = _Stub("noop", None, "unknown")


@contextmanager
def _approval(granted: bool) -> Iterator[None]:
    """Force the approval decision for one remediation, then restore it."""
    original = guardrails.approver
    guardrails.approver = lambda *args, **kwargs: granted
    try:
        yield
    finally:
        guardrails.approver = original


def _build_brain(backend: str, model: str):
    if backend == "ollama":
        from llm_brain import OllamaBrain

        return OllamaBrain(model=model)
    from agent import MockBrain

    return MockBrain()


# --------------------------------------------------------------------------- #
# Outcome rotation
# --------------------------------------------------------------------------- #
#
# Success-heavy so "normal" resolution dominates, with periodic failures so the
# denied/rolled_back/declined series accumulate non-trivial counts.

_OUTCOME_SCHEDULE = [
    "success", "success", "denied",
    "success", "success", "rolled_back",
    "success", "success", "declined",
    "success", "success", "no_diagnosis",
]
_SUCCESS_FAULTS = ["thermal", "latency", "error"]

# fault to inject per failing outcome (so the device is genuinely unhealthy)
_FAILING_FAULT = {
    "denied": "thermal",
    "rolled_back": "thermal",
    "no_diagnosis": "thermal",
    "declined": "error",
}


def _reset_params(device) -> None:
    """Restore default params so a freshly injected fault genuinely makes the
    device unhealthy (a prior recovery may have left it in eco / scaled up,
    which would otherwise mask a new thermal fault)."""
    device.set_param("batch_size", 8)
    device.set_param("worker_count", 2)
    device.set_param("power_mode", "normal")


def _healthy_count(fleet) -> int:
    return len(fleet.status()) - len(fleet.unhealthy())


def _inject(device, outcome: str, success_fault: str) -> None:
    """Phase 1: make the device genuinely unhealthy for this outcome."""
    _reset_params(device)
    fault = success_fault if outcome == "success" else _FAILING_FAULT[outcome]
    device.inject_fault(fault)


def _remediate(device, outcome: str, brain, audit) -> None:
    """Phase 2: remediate the (unhealthy) device to hit the target outcome."""
    if outcome == "success":
        agent.remediate(device, brain, audit)           # real brain resolves it
    elif outcome == "denied":
        agent.remediate(device, _OUT_OF_BOUNDS, audit)  # out-of-bounds -> DENY
    elif outcome == "rolled_back":
        agent.remediate(device, _WRONG_FIX, audit)      # wrong fix -> rolled_back
    elif outcome == "no_diagnosis":
        agent.remediate(device, _NOOP, audit)           # can't diagnose -> no_diagnosis
    elif outcome == "declined":
        with _approval(False):
            agent.remediate(device, _RESTART, audit)    # NEEDS_APPROVAL -> declined


def run(backend: str, model: str, port: int, period: float) -> None:
    if not metrics.start_metrics_server(port):
        raise SystemExit(
            "prometheus_client is not installed; `pip install -r requirements.txt`"
        )

    brain = _build_brain(backend, model)
    audit = AuditLog()
    fleet = default_fleet()

    # Publish the eval scorecard once at startup so the score gauges are populated.
    results = evaluate.run_eval(brain)
    summary = evaluate.summarize(results)
    metrics.set_eval_scores(
        summary["diagnosed_rate"], summary["in_bounds_rate"], summary["resolved_rate"]
    )

    model_label = model if backend == "ollama" else "mock"
    print(f"serving /metrics on :{port} (backend={backend}, model={model_label})")
    print("rotating outcomes every ~%.1fs; ctrl-c to stop" % period)

    devices = itertools.cycle(["edge-01", "edge-02", "edge-03", "edge-04"])
    success_faults = itertools.cycle(_SUCCESS_FAULTS)

    for outcome in itertools.cycle(_OUTCOME_SCHEDULE):
        device = fleet.get(next(devices))

        # Phase 1: inject the fault and publish the DIP, then leave a scrape
        # window where fleet_healthy < 4 while the fault is unresolved.
        _inject(device, outcome, next(success_faults))
        metrics.set_fleet_healthy(_healthy_count(fleet))
        time.sleep(period)

        # Phase 2: remediate to hit the outcome (records the result), then a
        # recovery sweep heals any still-unhealthy device — only unhealthy ones
        # are touched, so no healthy device is swept into a meaningless noop.
        _remediate(device, outcome, brain, audit)
        agent.sweep(fleet, brain, audit)
        metrics.set_fleet_healthy(_healthy_count(fleet))
        time.sleep(period)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve live agent metrics for Prometheus.")
    parser.add_argument("--backend", choices=("mock", "ollama"), default="mock")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--period", type=float, default=2.0, help="seconds between cycles")
    args = parser.parse_args()

    try:
        run(args.backend, args.model, args.port, args.period)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

"""export_run.py — run the real fleet + agent + eval and write run.json.

No UI. Uses the existing modules unchanged (fleet, guardrails, agent, evaluate,
and optionally llm_brain). Produces a single JSON document in the agreed shape:

    {
      "meta": {...},
      "timeline": [{"tick", "fleet": [...], "events": [...]}, ...],
      "eval": {"by_model": [...]}
    }

The scripted run starts from a healthy default_fleet(), staggers faults across
ticks, and sweeps with the chosen brain after each injection. Two ticks are
driven by small stub brains so the timeline always contains a DENY and a
rolled_back event regardless of backend; the rest use the chosen brain.
"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Iterator

import agent
import evaluate
import guardrails
from fleet import (
    ERROR_RATE_THRESHOLD,
    LATENCY_THRESHOLD_MS,
    TEMP_THRESHOLD_C,
    default_fleet,
)
from guardrails import AuditLog

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------- #
# Brains
# --------------------------------------------------------------------------- #


class _Stub:
    """A scripted brain that always proposes one fixed action (for demo ticks)."""

    def __init__(self, action: str, value: Any, diagnosis: str = "scripted") -> None:
        self._proposal = {
            "action": action,
            "value": value,
            "diagnosis": diagnosis,
            "reason": "scripted demonstration action",
        }

    def decide(self, view: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._proposal)


def _build_brain(backend: str, model: str):
    """Construct the chosen brain. Ollama is imported lazily so the mock default
    stays dependency-free and CI-safe."""
    if backend == "ollama":
        from llm_brain import OllamaBrain

        return OllamaBrain(model=model)
    from agent import MockBrain

    return MockBrain()


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #


def _round(obj: Any, ndigits: int = 4) -> Any:
    """Recursively round floats so the JSON is stable and readable."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: _round(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, ndigits) for v in obj]
    return obj


def _device_status(healthy: bool, device_events: List[Dict[str, Any]]) -> str:
    """Derived, display-oriented status for a device this tick.

    awaiting_approval: a NEEDS_APPROVAL event this tick that hasn't succeeded yet
    remediating:       not healthy (and not awaiting approval)
    healthy:           healthy
    """
    awaiting = any(
        e["verdict"] == "NEEDS_APPROVAL" and e["result"] != "success"
        for e in device_events
    )
    if awaiting:
        return "awaiting_approval"
    if not healthy:
        return "remediating"
    return "healthy"


def _fleet_frame(fleet, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One observable view per device, augmented with:
      - injected_fault: what was injected (ground truth), NOT the current problem;
        a device fixed by eco mode stays healthy with injected_fault still set.
      - status: derived display status from current health + this tick's events.
    (This is an export tool, so it may read truth — the brain still never does.)"""
    events_by_device: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        events_by_device.setdefault(e["device_id"], []).append(e)

    frame = []
    for view in fleet.status():
        entry = dict(view)
        entry["injected_fault"] = fleet.get(view["id"]).fault
        entry["status"] = _device_status(
            entry["healthy"], events_by_device.get(view["id"], [])
        )
        frame.append(entry)
    return _round(frame)


def _event_from_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a remediation trace into a timeline event."""
    verdict = trace.get("verdict")
    proposed = trace["proposed"]
    return {
        "device_id": trace["device_id"],
        "diagnosis": proposed["diagnosis"],
        "action": proposed["action"],
        "value": proposed["value"],
        "verdict": verdict["status"] if verdict else None,
        "approved": trace.get("approved"),
        "result": trace["result"],
    }


def _sweep_events(fleet, brain, audit: AuditLog) -> List[Dict[str, Any]]:
    return [_event_from_trace(t) for t in agent.sweep(fleet, brain, audit)]


@contextmanager
def _approval(granted: bool) -> Iterator[None]:
    """Temporarily force the approval decision for a scripted tick, then restore.

    `remediate` consults `guardrails.approver`; overriding it here lets us stage a
    DECLINED approval so a NEEDS_APPROVAL action is held (not applied)."""
    original = guardrails.approver
    guardrails.approver = lambda *args, **kwargs: granted
    try:
        yield
    finally:
        guardrails.approver = original


# --------------------------------------------------------------------------- #
# Scripted timeline
# --------------------------------------------------------------------------- #


def build_timeline(brain, audit: AuditLog) -> List[Dict[str, Any]]:
    """Drive a staggered fault/remediation run and capture every tick."""
    fleet = default_fleet()
    timeline: List[Dict[str, Any]] = []

    def frame(tick: int, events: List[Dict[str, Any]]) -> None:
        timeline.append(
            {"tick": tick, "fleet": _fleet_frame(fleet, events), "events": events}
        )

    # tick 0 — everything healthy, no events.
    frame(0, [])

    # tick 1 — thermal on edge-01, chosen brain remediates -> resolved.
    fleet.get("edge-01").inject_fault("thermal")
    frame(1, _sweep_events(fleet, brain, audit))

    # tick 2 — latency on edge-03, chosen brain remediates -> resolved.
    fleet.get("edge-03").inject_fault("latency")
    frame(2, _sweep_events(fleet, brain, audit))

    # tick 3 — error on edge-04 -> restart_worker is HIGH risk -> NEEDS_APPROVAL.
    fleet.get("edge-04").inject_fault("error")
    frame(3, _sweep_events(fleet, brain, audit))

    # tick 4 — thermal on edge-02; a stub proposes an out-of-bounds value -> DENY.
    fleet.get("edge-02").inject_fault("thermal")
    frame(4, _sweep_events(fleet, _Stub("scale_workers", 99, "thermal"), audit))

    # tick 5 — edge-02 still faulted; a wrong-but-in-bounds action -> rolled_back.
    frame(5, _sweep_events(fleet, _Stub("scale_workers", 8, "thermal"), audit))

    # tick 6 — chosen brain finally fixes edge-02 -> resolved (closure).
    frame(6, _sweep_events(fleet, brain, audit))

    # tick 7 — error on edge-01 with approval DECLINED: restart_worker is proposed,
    # hits NEEDS_APPROVAL, is held (not applied). edge-01 stays unhealthy and its
    # status is awaiting_approval for this frame.
    fleet.get("edge-01").inject_fault("error")
    with _approval(False):
        frame(7, _sweep_events(fleet, _Stub("restart_worker", None, "error"), audit))

    # tick 8 — approval granted; chosen brain fixes edge-01 -> resolved (closure).
    frame(8, _sweep_events(fleet, brain, audit))

    return timeline


# --------------------------------------------------------------------------- #
# Eval scorecard
# --------------------------------------------------------------------------- #


def build_eval(backend: str, model_label: str, brain) -> Dict[str, Any]:
    """Score the chosen brain against the scenarios and attach the scorecard."""
    results = evaluate.run_eval(brain)
    summary = evaluate.summarize(results)
    return {
        "by_model": [
            {
                "backend": backend,
                "model": model_label,
                "summary": _round(summary),
                "scenarios": _round(results),
            }
        ]
    }


# --------------------------------------------------------------------------- #
# Top level
# --------------------------------------------------------------------------- #


def export_run(
    backend: str = "mock", model: str = "qwen3:14b", out: str = "run.json"
) -> Dict[str, Any]:
    """Build the full run document, write it to `out`, and return it."""
    model_label = model if backend == "ollama" else "mock"
    brain = _build_brain(backend, model)
    audit = AuditLog()

    timeline = build_timeline(brain, audit)
    eval_section = build_eval(backend, model_label, brain)

    run = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "backend": backend,
            "model": model_label,
            "ticks": len(timeline),
            "thresholds": _round(
                {
                    "cpu_temp_c": TEMP_THRESHOLD_C,
                    "inference_latency_ms": LATENCY_THRESHOLD_MS,
                    "error_rate": ERROR_RATE_THRESHOLD,
                }
            ),
        },
        "timeline": timeline,
        "eval": eval_section,
    }

    with open(out, "w", encoding="utf-8") as f:
        json.dump(run, f, indent=2)

    return run


def _main() -> None:
    parser = argparse.ArgumentParser(description="Export a fleet/agent/eval run to JSON.")
    parser.add_argument("--backend", choices=("mock", "ollama"), default="mock")
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--out", default="run.json")
    args = parser.parse_args()

    run = export_run(backend=args.backend, model=args.model, out=args.out)
    s = run["eval"]["by_model"][0]["summary"]
    print(
        f"wrote {args.out}: {run['meta']['ticks']} ticks, "
        f"eval diagnosed {s['diagnosed']}/{s['total']} "
        f"resolved {s['resolved']}/{s['total']}"
    )


if __name__ == "__main__":
    _main()

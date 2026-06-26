"""agent.py — the closed remediation loop.

This is the ONLY module that (a) translates named actions into fleet's `apply()`
schema and (b) calls `guardrails.evaluate`. Every action passes through the
policy before it can touch the world; translation to the world schema happens
only after a decision permits it.

A "brain" is anything with:
    decide(view: dict) -> dict
returning a named action:
    {"action": <str>, "value": <any>, "diagnosis": <str>, "reason": <str>}
The brain sees only the device's observable view (telemetry) — never the hidden
fault.
"""

from __future__ import annotations

from typing import Any, Dict, List

import guardrails
from fleet import (
    ERROR_RATE_THRESHOLD,
    LATENCY_THRESHOLD_MS,
    TEMP_THRESHOLD_C,
)


# --------------------------------------------------------------------------- #
# Brain
# --------------------------------------------------------------------------- #


class MockBrain:
    """A rules-based stand-in for the future LLM. Reads telemetry from the view
    and proposes the named action that matches the breached metric."""

    def decide(self, view: Dict[str, Any]) -> Dict[str, Any]:
        t = view["telemetry"]

        if t["cpu_temp_c"] > TEMP_THRESHOLD_C:
            return {
                "action": "set_power_mode",
                "value": "eco",
                "diagnosis": "thermal",
                "reason": f"cpu_temp_c {t['cpu_temp_c']} over {TEMP_THRESHOLD_C}",
            }
        if t["inference_latency_ms"] > LATENCY_THRESHOLD_MS:
            return {
                "action": "scale_workers",
                "value": 4,
                "diagnosis": "latency",
                "reason": f"inference_latency_ms {t['inference_latency_ms']} over {LATENCY_THRESHOLD_MS}",
            }
        if t["error_rate"] > ERROR_RATE_THRESHOLD:
            return {
                "action": "restart_worker",
                "value": None,
                "diagnosis": "error",
                "reason": f"error_rate {t['error_rate']} over {ERROR_RATE_THRESHOLD}",
            }

        return {
            "action": "noop",
            "value": None,
            "diagnosis": "healthy",
            "reason": "no metric over threshold",
        }


# --------------------------------------------------------------------------- #
# Named action -> fleet apply() schema
# --------------------------------------------------------------------------- #
#
# Translation lives ONLY here, and is only ever reached after the policy permits
# the action.

_SET_PARAM = {
    "set_power_mode": "power_mode",
    "set_batch_size": "batch_size",
    "scale_workers": "worker_count",
}


def translate(action: str, value: Any) -> Dict[str, Any]:
    """Translate a named action into the world's apply() schema."""
    if action in _SET_PARAM:
        return {"type": "set_param", "param": _SET_PARAM[action], "value": value}
    if action == "restart_worker":
        return {"type": "restart_worker"}
    raise ValueError(f"no world translation for action {action!r}")


# --------------------------------------------------------------------------- #
# The closed loop
# --------------------------------------------------------------------------- #


def remediate(device, brain, audit, interactive: bool = False) -> Dict[str, Any]:
    """Run one decide -> gate -> act -> verify -> (rollback) cycle on a device.

    Returns a trace dict describing what happened. Records every phase to the
    audit log. The world is only ever changed through a policy-permitted action,
    and is restored from the snapshot if remediation fails to restore health.
    """
    snapshot = device.snapshot()
    trace: Dict[str, Any] = {
        "device_id": device.id,
        "healthy_before": device.is_healthy(),
    }

    # 1-2. Ask the brain for a proposed named action (from the view only).
    proposed = brain.decide(device.view())
    action, value = proposed["action"], proposed["value"]
    trace["proposed"] = proposed
    audit.record(phase="proposed", device_id=device.id, **proposed)

    # Brain had nothing to propose — a distinct outcome from a policy DENY.
    # Never send a noop to the policy gate.
    if action == "noop":
        trace["applied"] = False
        trace["result"] = "no_diagnosis"
        audit.record(phase="result", device_id=device.id, result="no_diagnosis")
        return trace

    # 3. Gate the action through policy — always.
    decision = guardrails.evaluate(action, value)
    trace["verdict"] = {"status": decision.status, "reason": decision.reason}
    audit.record(
        phase="verdict",
        device_id=device.id,
        action=action,
        value=value,
        status=decision.status,
        reason=decision.reason,
    )

    if decision.status == guardrails.DENY:
        trace["approved"] = None
        trace["applied"] = False
        trace["result"] = "denied"
        audit.record(phase="result", device_id=device.id, result="denied")
        return trace

    if decision.status == guardrails.NEEDS_APPROVAL:
        approved = guardrails.approver(action, value, interactive=interactive)
        trace["approved"] = approved
        audit.record(phase="approval", device_id=device.id, action=action, approved=approved)
        if not approved:
            trace["applied"] = False
            trace["result"] = "declined"
            audit.record(phase="result", device_id=device.id, result="declined")
            return trace
    else:  # ALLOW
        trace["approved"] = None

    # 4. Translate to the world schema and apply.
    world_action = translate(action, value)
    device.apply(world_action)
    trace["applied"] = True
    audit.record(phase="applied", device_id=device.id, world_action=world_action)

    # 5. Verify; roll back if not healthy.
    if device.is_healthy():
        trace["healthy_after"] = True
        trace["result"] = "success"
        audit.record(phase="result", device_id=device.id, result="success")
    else:
        device.restore(snapshot)
        trace["healthy_after"] = False
        trace["result"] = "rolled_back"
        audit.record(phase="result", device_id=device.id, result="rolled_back")

    return trace


def sweep(fleet, brain, audit, interactive: bool = False) -> List[Dict[str, Any]]:
    """Remediate every currently-unhealthy device in the fleet."""
    traces = []
    for device_id in fleet.unhealthy():
        traces.append(remediate(fleet.get(device_id), brain, audit, interactive=interactive))
    return traces


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


def _demo() -> None:
    from fleet import default_fleet
    from guardrails import AuditLog

    fleet = default_fleet()
    fleet.get("edge-01").inject_fault("thermal")
    fleet.get("edge-03").inject_fault("latency")
    fleet.get("edge-04").inject_fault("error")

    print("unhealthy before:", fleet.unhealthy())

    audit = AuditLog()
    brain = MockBrain()
    for trace in sweep(fleet, brain, audit, interactive=False):
        print(
            f"{trace['device_id']}: diagnosed={trace['proposed']['diagnosis']} "
            f"action={trace['proposed']['action']} verdict={trace['verdict']['status']} "
            f"result={trace['result']}"
        )

    print("unhealthy after: ", fleet.unhealthy())


if __name__ == "__main__":
    _demo()

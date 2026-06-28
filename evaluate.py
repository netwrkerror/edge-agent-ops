"""evaluate.py — score ANY brain against the evaluation scenarios.

A brain is anything with `decide(view) -> named action` (the same interface
`agent.MockBrain` implements). The brain under test only ever sees
`device.view()`.

This module is the SCORER, so it is allowed to read ground truth — the device's
true injected fault and `fleet.remediation_for` — to judge the brain. It scores
three INDEPENDENT booleans per scenario and never collapses them into one
pass/fail:

  diagnosed : the brain's proposed action matches the correct action FAMILY for
              the injected fault (by name, not value).
  in_bounds : guardrails.evaluate(proposed) returns a non-DENY verdict.
  resolved  : after the full agent.remediate loop, the device is healthy
              (measured from telemetry, not from the brain's claim).
"""

from __future__ import annotations

from typing import Any, Dict, List

import agent
import guardrails
from fleet import remediation_for
from guardrails import AuditLog
from scenarios import SCENARIOS


# --------------------------------------------------------------------------- #
# Ground-truth: the correct action family per fault
# --------------------------------------------------------------------------- #
#
# remediation_for(fault) returns the canonical action in fleet's apply() schema;
# we map that back to the named-action family the brain is expected to propose.

_PARAM_TO_FAMILY = {
    "power_mode": "set_power_mode",
    "worker_count": "scale_workers",
    "batch_size": "set_batch_size",
}


def correct_action_family(fault: str) -> str:
    """The named-action family that correctly remediates the given fault."""
    canonical = remediation_for(fault)
    if canonical["type"] == "restart_worker":
        return "restart_worker"
    return _PARAM_TO_FAMILY[canonical["param"]]


# --------------------------------------------------------------------------- #
# Scoring one scenario
# --------------------------------------------------------------------------- #


def score_scenario(brain, scenario, audit: AuditLog) -> Dict[str, Any]:
    """Score a single scenario on the three independent dimensions.

    The brain is consulted EXACTLY ONCE; all three scores derive from that one
    decision. `resolved` then drives the real gate->apply->verify->rollback
    machinery on that same decision via `remediate_with_decision` — the loop
    never calls `decide()` a second time.
    """
    device = scenario.build()
    decision = brain.decide(device.view())  # the single decision
    action, value = decision["action"], decision["value"]

    # --- diagnosed + in_bounds: judged from that one proposal ----------------
    diagnosed = action == correct_action_family(scenario.fault)

    verdict = guardrails.evaluate(action, value)
    in_bounds = verdict.status != guardrails.DENY

    # --- resolved: apply the SAME decision through the agent's machinery ------
    trace = agent.remediate_with_decision(device, decision, audit)
    resolved = device.is_healthy()

    return {
        "name": scenario.name,
        "fault": scenario.fault,
        "proposed": {"action": action, "value": value},
        "diagnosed": diagnosed,
        "in_bounds": in_bounds,
        "verdict": verdict.status,
        "resolved": resolved,
        "result": trace["result"],
    }


# --------------------------------------------------------------------------- #
# Running and aggregating
# --------------------------------------------------------------------------- #


def run_eval(brain, scenarios=SCENARIOS) -> List[Dict[str, Any]]:
    """Score a brain across all scenarios; one result dict per scenario."""
    audit = AuditLog()
    return [score_scenario(brain, s, audit) for s in scenarios]


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate counts and rates for each dimension, kept separate."""
    total = len(results)

    def count(key: str) -> int:
        return sum(1 for r in results if r[key])

    def rate(n: int) -> float:
        return round(n / total, 3) if total else 0.0

    summary = {"total": total}
    for dim in ("diagnosed", "in_bounds", "resolved"):
        n = count(dim)
        summary[dim] = n
        summary[f"{dim}_rate"] = rate(n)
    return summary


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def report(brain, scenarios=SCENARIOS) -> List[Dict[str, Any]]:
    """Run the eval and print a per-scenario table plus a summary line."""
    results = run_eval(brain, scenarios)

    header = (
        f"{'scenario':20} {'fault':8} {'proposed':24} "
        f"{'diag':5} {'bnds':5} {'verdict':16} {'rslv':5} {'result':12}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        proposed = f"{r['proposed']['action']}({r['proposed']['value']})"
        print(
            f"{r['name']:20} {r['fault']:8} {proposed:24} "
            f"{_yn(r['diagnosed']):5} {_yn(r['in_bounds']):5} "
            f"{r['verdict']:16} {_yn(r['resolved']):5} {r['result']:12}"
        )

    s = summarize(results)
    print("-" * len(header))
    print(
        f"summary: diagnosed {s['diagnosed']}/{s['total']} ({s['diagnosed_rate']})  "
        f"in_bounds {s['in_bounds']}/{s['total']} ({s['in_bounds_rate']})  "
        f"resolved {s['resolved']}/{s['total']} ({s['resolved_rate']})"
    )
    return results


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


def _build_brain(backend: str, model: str):
    """Construct the requested brain. Ollama is imported lazily so the default
    mock path stays dependency-free."""
    if backend == "ollama":
        from llm_brain import OllamaBrain

        return OllamaBrain(model=model)
    from agent import MockBrain

    return MockBrain()


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Score a brain against the scenarios.")
    parser.add_argument("--backend", choices=("mock", "ollama"), default="mock")
    parser.add_argument("--model", default="qwen3:14b")
    args = parser.parse_args()

    report(_build_brain(args.backend, args.model))


if __name__ == "__main__":
    _main()

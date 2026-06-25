"""guardrails.py — the policy/safety layer that gates every action.

This module DECIDES; it never acts. It does not import the fleet's Device or
its apply logic, never mutates fleet state, and `evaluate()` is a pure function.

The valid parameter ranges are imported from fleet.py, which is the single
source of truth — they are never hardcoded here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fleet import BATCH_SIZE_RANGE, POWER_MODES, WORKER_COUNT_RANGE


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #

ALLOW = "ALLOW"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
DENY = "DENY"

LOW = "low"
HIGH = "HIGH"


@dataclass(frozen=True)
class Decision:
    status: str  # one of ALLOW | NEEDS_APPROVAL | DENY
    reason: str


# --------------------------------------------------------------------------- #
# Whitelist of named actions
# --------------------------------------------------------------------------- #
#
# Each entry pairs a validator (returns None if the value is acceptable, else a
# specific human-readable reason naming the violated bound) with a risk level.
# Anything not in this dict is denied.


def _validate_power_mode(value: Any) -> Optional[str]:
    if value not in POWER_MODES:
        return f"power_mode must be one of {tuple(POWER_MODES)}, got {value!r}"
    return None


def _is_plain_int(value: Any) -> bool:
    # bool is a subclass of int; reject it so True/False can't slip through.
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_int_in_range(value: Any, name: str, bounds) -> Optional[str]:
    lo, hi = bounds
    if not _is_plain_int(value):
        return f"{name} must be an int within {lo}..{hi}, got {type(value).__name__}"
    if not (lo <= value <= hi):
        return f"{name} must be an int within {lo}..{hi}, got {value}"
    return None


def _validate_batch_size(value: Any) -> Optional[str]:
    return _validate_int_in_range(value, "batch_size", BATCH_SIZE_RANGE)


def _validate_workers(value: Any) -> Optional[str]:
    return _validate_int_in_range(value, "worker_count", WORKER_COUNT_RANGE)


def _validate_ignored(value: Any) -> Optional[str]:
    return None  # value is ignored for this action


# action name -> (validator, risk level)
WHITELIST: Dict[str, Dict[str, Any]] = {
    "set_power_mode": {"validate": _validate_power_mode, "risk": LOW},
    "set_batch_size": {"validate": _validate_batch_size, "risk": LOW},
    "scale_workers": {"validate": _validate_workers, "risk": LOW},
    "restart_worker": {"validate": _validate_ignored, "risk": HIGH},
}


# --------------------------------------------------------------------------- #
# evaluate — the pure policy decision
# --------------------------------------------------------------------------- #


def evaluate(action: str, value: Any = None) -> Decision:
    """Decide whether an action is allowed. Pure: no side effects, no mutation.

    Rules, in order:
      unknown action            -> DENY ("not in whitelist")
      wrong type / out of range -> DENY (specific reason naming the bound)
      valid + HIGH risk         -> NEEDS_APPROVAL
      otherwise                 -> ALLOW
    """
    spec = WHITELIST.get(action)
    if spec is None:
        return Decision(DENY, f"action {action!r} not in whitelist")

    invalid = spec["validate"](value)
    if invalid is not None:
        return Decision(DENY, invalid)

    if spec["risk"] == HIGH:
        return Decision(NEEDS_APPROVAL, f"action {action!r} is HIGH risk; needs approval")

    return Decision(ALLOW, f"action {action!r} within policy")


# --------------------------------------------------------------------------- #
# Approval workflow
# --------------------------------------------------------------------------- #


def approver(action: str, value: Any = None, interactive: bool = False) -> bool:
    """Decide approval for an action that NEEDS_APPROVAL.

    interactive=False: auto-approve (eval/batch mode) and return True.
    interactive=True : prompt the human (y/N) and return their choice.
    """
    if not interactive:
        return True

    prompt = f"Approve HIGH-risk action {action!r}"
    if value is not None:
        prompt += f" (value={value!r})"
    prompt += "? [y/N] "
    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #


class AuditLog:
    """Append-only record of events, in memory and optionally to a file.

    Each event is timestamped (UTC, ISO 8601). If a file path was provided at
    construction, each event is also appended as one JSON line.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self.events: List[Dict[str, Any]] = []

    def record(self, **fields: Any) -> Dict[str, Any]:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **fields}
        self.events.append(event)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        return event


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #


def _demo() -> None:
    audit = AuditLog()
    samples = [
        ("set_power_mode", "eco"),
        ("set_power_mode", "ludicrous"),
        ("set_batch_size", 16),
        ("set_batch_size", 999),
        ("scale_workers", 4),
        ("scale_workers", True),
        ("restart_worker", None),
        ("format_disk", None),
    ]
    for action, value in samples:
        decision = evaluate(action, value)
        audit.record(action=action, value=value, status=decision.status, reason=decision.reason)
        print(f"{decision.status:14} {action}({value!r}) -> {decision.reason}")


if __name__ == "__main__":
    _demo()

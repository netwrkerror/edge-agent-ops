"""llm_brain.py — a brain backed by a local LLM via Ollama.

Implements the same interface as `agent.MockBrain`:
    decide(view: dict) -> {"action", "value", "diagnosis", "reason"}

It talks to a local Ollama server over HTTP using only the standard library.
A bad or unreachable model must NEVER raise out of `decide()`; it degrades to a
safe `noop` so the rest of the pipeline (policy gate, verify, rollback) stays in
control.

The allowed actions, their ranges, and the health thresholds are imported from
fleet.py so the prompt can't drift from what guardrails/fleet actually enforce.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from fleet import (
    BATCH_SIZE_RANGE,
    ERROR_RATE_THRESHOLD,
    LATENCY_THRESHOLD_MS,
    POWER_MODES,
    TEMP_THRESHOLD_C,
    WORKER_COUNT_RANGE,
)


def _safe_noop(detail: str) -> Dict[str, Any]:
    """The fallback decision when the model can't be trusted/parsed."""
    return {"action": "noop", "value": None, "diagnosis": "parse_error", "reason": detail}


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the first JSON object from model output.

    Handles raw JSON, ```json fenced blocks, and leading/trailing prose by
    falling back to the first '{' .. last '}' span. Returns None on failure.
    """
    if not text:
        return None

    # 1) Try the whole thing as-is.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass

    # 2) Strip markdown code fences and retry the span between braces.
    cleaned = text.replace("```json", "```").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(cleaned[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def parse_decision(content: str) -> Dict[str, Any]:
    """Turn raw model output into a named-action dict, or the safe noop fallback.

    Never raises: unparseable, empty, or action-less content degrades to the
    noop/parse_error fallback. Missing diagnosis/reason are filled with defaults;
    a missing or empty `action` is treated as no decision.
    """
    obj = _extract_json_object(content)
    if obj is None:
        return _safe_noop("could not parse JSON from model content")

    action = obj.get("action")
    if not action:
        return _safe_noop("model response missing/empty action")

    return {
        "action": action,
        "value": obj.get("value"),
        "diagnosis": obj.get("diagnosis", "unknown"),
        "reason": obj.get("reason", ""),
    }


class OllamaBrain:
    """A brain that asks a local Ollama model which action to take."""

    def __init__(
        self,
        model: str = "qwen3:14b",
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        # Token accounting from the most recent call (None until a call returns).
        self.last_eval_count: Optional[int] = None
        self.last_prompt_eval_count: Optional[int] = None
        self.last_total_tokens: Optional[int] = None

    # ---- prompt construction --------------------------------------------- #

    def _system_prompt(self) -> str:
        batch_lo, batch_hi = BATCH_SIZE_RANGE
        worker_lo, worker_hi = WORKER_COUNT_RANGE
        modes = " | ".join(f'"{m}"' for m in POWER_MODES)
        return (
            "You are a remediation brain for a fleet of edge AI inference nodes.\n"
            "You are given one device's observable telemetry and parameters and must\n"
            "choose exactly ONE corrective action.\n\n"
            "A device is healthy iff ALL of these hold:\n"
            f"  cpu_temp_c <= {TEMP_THRESHOLD_C}\n"
            f"  inference_latency_ms <= {LATENCY_THRESHOLD_MS}\n"
            f"  error_rate <= {ERROR_RATE_THRESHOLD}\n\n"
            "Allowed actions (you MUST pick one of these names exactly):\n"
            f"  set_power_mode  value: {modes}\n"
            f"  set_batch_size  value: integer {batch_lo}..{batch_hi}\n"
            f"  scale_workers   value: integer {worker_lo}..{worker_hi}\n"
            "  restart_worker  value: null\n\n"
            "Guidance: overheating -> set_power_mode \"eco\"; high latency ->\n"
            "scale_workers to a higher count; high error_rate -> restart_worker.\n\n"
            "Respond with ONLY a single JSON object and nothing else:\n"
            '{"diagnosis": <short string>, "action": <one allowed name>, '
            '"value": <value or null>, "reason": <short string>}'
        )

    def _user_prompt(self, view: Dict[str, Any]) -> str:
        return (
            "Here is the device. Diagnose it and choose one action.\n\n"
            + json.dumps(view, indent=2)
        )

    # ---- the call -------------------------------------------------------- #

    def _post_chat(self, view: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(view)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _record_tokens(self, response: Dict[str, Any]) -> None:
        self.last_prompt_eval_count = response.get("prompt_eval_count")
        self.last_eval_count = response.get("eval_count")
        if self.last_prompt_eval_count is not None or self.last_eval_count is not None:
            self.last_total_tokens = (self.last_prompt_eval_count or 0) + (
                self.last_eval_count or 0
            )

    # ---- interface ------------------------------------------------------- #

    def decide(self, view: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the model for an action. Never raises; degrades to noop."""
        try:
            response = self._post_chat(view)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            return _safe_noop(f"ollama unreachable: {exc}")
        except ValueError as exc:  # bad JSON envelope from the server
            return _safe_noop(f"bad response envelope: {exc}")

        self._record_tokens(response)

        content = (response.get("message") or {}).get("content", "")
        return parse_decision(content)


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _smoke_test() -> None:
    from fleet import Device

    device = Device(id="edge-smoke", site="plant-eval")
    device.inject_fault("thermal")

    brain = OllamaBrain()
    decision = brain.decide(device.view())

    print("decision:", json.dumps(decision, indent=2))
    print(
        "tokens:",
        {
            "prompt_eval_count": brain.last_prompt_eval_count,
            "eval_count": brain.last_eval_count,
            "total": brain.last_total_tokens,
        },
    )


if __name__ == "__main__":
    _smoke_test()

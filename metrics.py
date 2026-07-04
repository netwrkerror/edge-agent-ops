"""metrics.py — Prometheus instrumentation for the agent's behavior.

This is the ONLY module that touches prometheus_client. The core (fleet,
guardrails, agent, evaluate) stays dependency-free: callers use the thin API
below and never import prometheus types. If prometheus_client is not installed,
every function degrades to a no-op so the core still runs.

Metric names use the `edgeops_` prefix. Counters are declared without the
`_total` suffix because prometheus_client appends it, yielding the documented
exposed names (e.g. `edgeops_remediations_total`).
"""

from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    _PROM_AVAILABLE = True
except ImportError:  # keep the core runnable without the optional dependency
    _PROM_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Metric objects (None when prometheus_client is absent)
# --------------------------------------------------------------------------- #

if _PROM_AVAILABLE:
    _remediations = Counter(
        "edgeops_remediations",
        "Remediation outcomes, labeled by result.",
        ["result"],
    )
    _decision_latency = Histogram(
        "edgeops_decision_latency_seconds",
        "Per-decision wall-clock time (brain.decide).",
    )
    _approval_holds = Counter(
        "edgeops_approval_holds",
        "Times a high-risk action hit NEEDS_APPROVAL.",
    )
    _fleet_healthy = Gauge(
        "edgeops_fleet_healthy",
        "Current count of healthy devices in the fleet.",
    )
    _eval_score = Gauge(
        "edgeops_eval_score",
        "Latest eval rates in [0, 1], labeled by dimension.",
        ["dimension"],
    )
else:
    _remediations = None
    _decision_latency = None
    _approval_holds = None
    _fleet_healthy = None
    _eval_score = None


# --------------------------------------------------------------------------- #
# Thin API — safe to call whether or not prometheus_client is installed
# --------------------------------------------------------------------------- #


def is_enabled() -> bool:
    """True when prometheus_client is available and metrics are live."""
    return _PROM_AVAILABLE


def record_remediation(result: str) -> None:
    """Count one remediation outcome (success|denied|rolled_back|no_diagnosis|declined)."""
    if _remediations is not None:
        _remediations.labels(result=result).inc()


def observe_decision_latency(seconds: float) -> None:
    """Record one per-decision wall-clock duration."""
    if _decision_latency is not None:
        _decision_latency.observe(seconds)


def record_approval_hold() -> None:
    """Count one NEEDS_APPROVAL hold on a high-risk action."""
    if _approval_holds is not None:
        _approval_holds.inc()


def set_fleet_healthy(n: int) -> None:
    """Set the current count of healthy devices."""
    if _fleet_healthy is not None:
        _fleet_healthy.set(n)


def set_eval_scores(
    diagnosed_rate: float, in_bounds_rate: float, resolved_rate: float
) -> None:
    """Set the three eval dimension rates (each 0..1)."""
    if _eval_score is not None:
        _eval_score.labels(dimension="diagnosed").set(diagnosed_rate)
        _eval_score.labels(dimension="in_bounds").set(in_bounds_rate)
        _eval_score.labels(dimension="resolved").set(resolved_rate)


def start_metrics_server(port: int = 8000) -> bool:
    """Expose /metrics via prometheus_client's HTTP server.

    Returns True if the server started, False if prometheus_client is absent.
    """
    if not _PROM_AVAILABLE:
        return False
    start_http_server(port)
    return True

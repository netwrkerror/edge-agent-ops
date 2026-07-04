"""Tests for metrics.py — counter/histogram/gauge updates and the no-op fallback.

No server is started. When prometheus_client is installed we introspect the
default registry; the no-op path is exercised by nulling the metric handles.
"""

import pytest

import metrics

prometheus_client = pytest.importorskip("prometheus_client")
from prometheus_client import REGISTRY


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {})


def test_record_remediation_increments_labeled_counter():
    before = _sample("edgeops_remediations_total", {"result": "success"}) or 0.0
    metrics.record_remediation("success")
    after = _sample("edgeops_remediations_total", {"result": "success"})
    assert after == before + 1


def test_record_remediation_tracks_each_result_separately():
    before = _sample("edgeops_remediations_total", {"result": "denied"}) or 0.0
    metrics.record_remediation("denied")
    metrics.record_remediation("denied")
    after = _sample("edgeops_remediations_total", {"result": "denied"})
    assert after == before + 2


def test_observe_decision_latency_updates_histogram():
    before_count = _sample("edgeops_decision_latency_seconds_count") or 0.0
    before_sum = _sample("edgeops_decision_latency_seconds_sum") or 0.0
    metrics.observe_decision_latency(0.25)
    assert _sample("edgeops_decision_latency_seconds_count") == before_count + 1
    assert _sample("edgeops_decision_latency_seconds_sum") == pytest.approx(before_sum + 0.25)


def test_record_approval_hold_increments():
    before = _sample("edgeops_approval_holds_total") or 0.0
    metrics.record_approval_hold()
    assert _sample("edgeops_approval_holds_total") == before + 1


def test_set_fleet_healthy_sets_gauge():
    metrics.set_fleet_healthy(3)
    assert _sample("edgeops_fleet_healthy") == 3
    metrics.set_fleet_healthy(4)
    assert _sample("edgeops_fleet_healthy") == 4


def test_set_eval_scores_sets_each_dimension():
    metrics.set_eval_scores(1.0, 0.5, 0.25)
    assert _sample("edgeops_eval_score", {"dimension": "diagnosed"}) == 1.0
    assert _sample("edgeops_eval_score", {"dimension": "in_bounds"}) == 0.5
    assert _sample("edgeops_eval_score", {"dimension": "resolved"}) == 0.25


def test_noop_fallback_when_lib_absent(monkeypatch):
    # Simulate prometheus_client being unavailable by nulling the handles.
    for handle in ("_remediations", "_decision_latency", "_approval_holds",
                   "_fleet_healthy", "_eval_score"):
        monkeypatch.setattr(metrics, handle, None)

    # None of these should raise or touch the (absent) backend.
    metrics.record_remediation("success")
    metrics.observe_decision_latency(0.1)
    metrics.record_approval_hold()
    metrics.set_fleet_healthy(2)
    metrics.set_eval_scores(1.0, 1.0, 1.0)


def test_agent_wiring_increments_remediation_counter():
    # Prove the agent call site actually feeds the metric, end to end.
    from fleet import Device
    from guardrails import AuditLog
    import agent

    before = _sample("edgeops_remediations_total", {"result": "success"}) or 0.0
    device = Device(id="edge-metric", site="plant-test")
    device.inject_fault("thermal")
    agent.remediate(device, agent.MockBrain(), AuditLog())
    after = _sample("edgeops_remediations_total", {"result": "success"})
    assert after == before + 1

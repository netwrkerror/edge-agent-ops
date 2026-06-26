"""Tests for the closed remediation loop (agent.py)."""

import pytest

import guardrails
from agent import MockBrain, remediate, sweep
from fleet import Device, Fleet
from guardrails import AuditLog


def make_device(fault=None) -> Device:
    d = Device(id="edge-test", site="plant-test")
    if fault:
        d.inject_fault(fault)
    return d


class StubBrain:
    """A brain that always proposes a fixed named action."""

    def __init__(self, action, value=None, diagnosis="stub", reason="stub"):
        self._proposal = {
            "action": action,
            "value": value,
            "diagnosis": diagnosis,
            "reason": reason,
        }

    def decide(self, view):
        return dict(self._proposal)


def phases(audit):
    return [e["phase"] for e in audit.events]


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def test_thermal_fault_is_remediated_to_healthy():
    d = make_device("thermal")
    trace = remediate(d, MockBrain(), AuditLog())
    assert trace["result"] == "success"
    assert d.is_healthy()


@pytest.mark.parametrize("fault", ["latency", "error"])
def test_latency_and_error_faults_are_remediated_to_healthy(fault):
    d = make_device(fault)
    trace = remediate(d, MockBrain(), AuditLog())
    assert trace["result"] == "success"
    assert d.is_healthy()


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_wrong_but_in_bounds_action_rolls_back_to_exact_snapshot():
    d = make_device("thermal")
    before = d.snapshot()
    # scale_workers is in-bounds (ALLOW) but does nothing for a thermal fault.
    wrong = StubBrain("scale_workers", 8)
    trace = remediate(d, wrong, AuditLog())
    assert trace["result"] == "rolled_back"
    assert d.snapshot() == before  # restored exactly, including params
    assert not d.is_healthy()


# --------------------------------------------------------------------------- #
# DENY
# --------------------------------------------------------------------------- #


def test_denied_action_changes_nothing():
    d = make_device("thermal")
    before = d.snapshot()
    deny = StubBrain("set_batch_size", 999)  # out of bounds -> DENY
    trace = remediate(d, deny, AuditLog())
    assert trace["verdict"]["status"] == guardrails.DENY
    assert trace["result"] == "denied"
    assert trace["applied"] is False
    assert d.snapshot() == before  # world untouched


# --------------------------------------------------------------------------- #
# No diagnosis
# --------------------------------------------------------------------------- #


def test_unhealthy_but_undiagnosed_records_no_diagnosis():
    d = make_device("thermal")  # unhealthy, but the brain proposes nothing
    before = d.snapshot()
    noop = StubBrain("noop", None)
    trace = remediate(d, noop, AuditLog())
    assert trace["result"] == "no_diagnosis"  # distinct from "denied"
    assert trace["applied"] is False
    assert d.snapshot() == before  # device untouched


# --------------------------------------------------------------------------- #
# Approval routing
# --------------------------------------------------------------------------- #


def test_restart_worker_proceeds_under_auto_approval():
    d = make_device("error")
    trace = remediate(d, MockBrain(), AuditLog(), interactive=False)
    assert trace["verdict"]["status"] == guardrails.NEEDS_APPROVAL
    assert trace["approved"] is True
    assert trace["result"] == "success"
    assert d.is_healthy()


def test_declined_approval_changes_nothing(monkeypatch):
    d = make_device("error")
    before = d.snapshot()
    monkeypatch.setattr(guardrails, "approver", lambda *a, **k: False)
    trace = remediate(d, MockBrain(), AuditLog())
    assert trace["approved"] is False
    assert trace["result"] == "declined"
    assert trace["applied"] is False
    assert d.snapshot() == before  # still faulted, untouched


# --------------------------------------------------------------------------- #
# Audit phases
# --------------------------------------------------------------------------- #


def test_audit_records_phases_for_happy_path():
    audit = AuditLog()
    remediate(make_device("thermal"), MockBrain(), audit)
    assert phases(audit) == ["proposed", "verdict", "applied", "result"]
    assert audit.events[-1]["result"] == "success"


def test_audit_records_phases_for_rollback():
    audit = AuditLog()
    remediate(make_device("thermal"), StubBrain("scale_workers", 8), audit)
    assert phases(audit) == ["proposed", "verdict", "applied", "result"]
    assert audit.events[-1]["result"] == "rolled_back"


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #


def test_sweep_only_touches_unhealthy_devices():
    healthy = Device(id="edge-ok", site="s")
    faulted = Device(id="edge-bad", site="s")
    faulted.inject_fault("thermal")
    fleet = Fleet([healthy, faulted])

    healthy_before = healthy.snapshot()
    traces = sweep(fleet, MockBrain(), AuditLog())

    assert [t["device_id"] for t in traces] == ["edge-bad"]  # only the unhealthy one
    assert healthy.snapshot() == healthy_before  # untouched
    assert fleet.unhealthy() == []  # faulted one fixed

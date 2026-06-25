"""Tests for the policy/safety layer (guardrails.py)."""

from fleet import BATCH_SIZE_RANGE, WORKER_COUNT_RANGE
from guardrails import (
    ALLOW,
    DENY,
    NEEDS_APPROVAL,
    AuditLog,
    evaluate,
)


def test_set_power_mode_eco_is_allowed():
    assert evaluate("set_power_mode", "eco").status == ALLOW


def test_set_batch_size_out_of_range_is_denied():
    too_big = BATCH_SIZE_RANGE[1] + 1
    assert evaluate("set_batch_size", too_big).status == DENY


def test_set_batch_size_wrong_type_is_denied():
    assert evaluate("set_batch_size", "big").status == DENY


def test_scale_workers_valid_is_allowed():
    assert evaluate("scale_workers", 4).status == ALLOW


def test_scale_workers_out_of_range_is_denied():
    too_many = WORKER_COUNT_RANGE[1] + 1
    assert evaluate("scale_workers", too_many).status == DENY


def test_unknown_action_is_denied_not_in_whitelist():
    decision = evaluate("delete_everything")
    assert decision.status == DENY
    assert "whitelist" in decision.reason


def test_restart_worker_needs_approval():
    assert evaluate("restart_worker").status == NEEDS_APPROVAL


def test_evaluate_is_pure():
    first = evaluate("set_batch_size", 999)
    second = evaluate("set_batch_size", 999)
    assert first == second  # equal results
    # frozen dataclass: no attribute mutation possible -> no shared state to leak
    assert evaluate("set_power_mode", "eco").status == ALLOW  # unaffected by above


def test_out_of_range_reason_names_the_bound():
    lo, hi = BATCH_SIZE_RANGE
    reason = evaluate("set_batch_size", hi + 1).reason
    assert str(lo) in reason and str(hi) in reason


def test_auditlog_appends_in_order_and_never_edits_past_events():
    log = AuditLog()
    log.record(action="set_power_mode", value="eco")
    first_snapshot = dict(log.events[0])  # copy of the first event as recorded

    log.record(action="restart_worker", value=None)

    assert len(log.events) == 2
    assert log.events[0]["action"] == "set_power_mode"  # order preserved
    assert log.events[1]["action"] == "restart_worker"
    assert log.events[0] == first_snapshot  # past event untouched by the new one

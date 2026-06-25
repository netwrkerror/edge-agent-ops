"""Tests for the fleet simulator (fleet.py)."""

import pytest

from fleet import (
    ERROR_RATE_THRESHOLD,
    LATENCY_THRESHOLD_MS,
    TEMP_THRESHOLD_C,
    Device,
    remediation_for,
)


def make_device() -> Device:
    """A fresh device with default params and no fault."""
    return Device(id="edge-test", site="plant-test")


def test_fresh_device_is_healthy():
    assert make_device().is_healthy()


def test_thermal_fault_overheats_and_unhealthies():
    d = make_device()
    d.inject_fault("thermal")
    assert d.telemetry().cpu_temp_c > TEMP_THRESHOLD_C
    assert not d.is_healthy()


def test_latency_fault_slows_and_unhealthies():
    d = make_device()
    d.inject_fault("latency")
    assert d.telemetry().inference_latency_ms > LATENCY_THRESHOLD_MS
    assert not d.is_healthy()


def test_error_fault_raises_error_rate_and_unhealthies():
    d = make_device()
    d.inject_fault("error")
    assert d.telemetry().error_rate > ERROR_RATE_THRESHOLD
    assert not d.is_healthy()


@pytest.mark.parametrize("fault", ["thermal", "latency", "error"])
def test_correct_remediation_restores_health(fault):
    d = make_device()
    d.inject_fault(fault)
    assert not d.is_healthy()
    d.apply(remediation_for(fault))
    assert d.is_healthy()


def test_wrong_action_does_not_restore_health():
    d = make_device()
    d.inject_fault("thermal")
    # Scaling workers is the latency fix, not the thermal one.
    d.apply({"type": "set_param", "param": "worker_count", "value": 8})
    assert not d.is_healthy()


def test_snapshot_mutate_restore_round_trips():
    d = make_device()
    snap = d.snapshot()

    d.inject_fault("thermal")
    d.set_param("power_mode", "turbo")
    d.set_param("batch_size", 64)
    d.set_param("worker_count", 8)
    assert d.snapshot() != snap

    d.restore(snap)
    assert d.snapshot() == snap


def test_telemetry_is_deterministic():
    d = make_device()
    d.inject_fault("latency")
    first = d.telemetry()
    second = d.telemetry()
    assert first == second
    assert first.as_dict() == second.as_dict()

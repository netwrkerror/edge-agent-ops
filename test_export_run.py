"""Tests for the run exporter (export_run.py). Mock backend only — no live model."""

import json

from export_run import export_run


def _all_event_results(timeline):
    return [e["result"] for frame in timeline for e in frame["events"]]


def test_export_run_writes_expected_shape(tmp_path):
    out = tmp_path / "run.json"
    returned = export_run(backend="mock", out=str(out))

    # The file exists and parses.
    data = json.loads(out.read_text())

    # Top-level shape.
    assert set(data) == {"meta", "timeline", "eval"}
    assert data["meta"]["backend"] == "mock"
    assert data["meta"]["model"] == "mock"
    assert returned["meta"]["ticks"] == len(data["timeline"])

    # Timeline: non-empty, each frame well-formed.
    timeline = data["timeline"]
    assert timeline, "timeline should be non-empty"
    for frame in timeline:
        assert "tick" in frame
        assert isinstance(frame["fleet"], list) and frame["fleet"]
        assert isinstance(frame["events"], list)
        for device in frame["fleet"]:
            assert {"id", "telemetry", "healthy", "injected_fault", "status"} <= set(device)
            assert device["status"] in {"healthy", "remediating", "awaiting_approval"}

    # injected_fault records what was injected, not the current problem: a device
    # fixed by eco mode is healthy while still carrying its injected_fault.
    assert any(
        d["healthy"] and d["injected_fault"] is not None
        for frame in timeline
        for d in frame["fleet"]
    ), "expected a healthy device that still has an injected_fault"

    # At least one resolved and one denied event somewhere in the run.
    results = _all_event_results(timeline)
    assert "success" in results, "expected a resolved remediation"
    assert "denied" in results, "expected a denied action"

    # Eval scorecard present with the three counts.
    by_model = data["eval"]["by_model"]
    assert by_model, "eval.by_model should be non-empty"
    summary = by_model[0]["summary"]
    for key in ("diagnosed", "in_bounds", "resolved", "total"):
        assert key in summary


def test_export_run_covers_all_interesting_event_kinds(tmp_path):
    out = tmp_path / "run.json"
    data = export_run(backend="mock", out=str(out))

    results = set(_all_event_results(data["timeline"]))
    assert {"success", "denied", "rolled_back"} <= results

    # A genuinely HELD approval: NEEDS_APPROVAL that did not (yet) succeed,
    # not just an auto-approved one.
    held = [
        e
        for frame in data["timeline"]
        for e in frame["events"]
        if e["verdict"] == "NEEDS_APPROVAL" and e["result"] != "success"
    ]
    assert held, "expected a held (declined) NEEDS_APPROVAL event"

    # ...and the device shows awaiting_approval somewhere in the timeline.
    statuses = {d["status"] for frame in data["timeline"] for d in frame["fleet"]}
    assert "awaiting_approval" in statuses

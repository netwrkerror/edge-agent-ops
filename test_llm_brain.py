"""Tests for OllamaBrain's parsing/fallback logic.

These tests never touch a real server: they exercise the module-level
`parse_decision` (and `_extract_json_object`) by feeding raw response strings,
proving the brain degrades safely and deterministically.
"""

import pytest

from llm_brain import parse_decision


def test_clean_json_object_parses_to_named_action():
    content = '{"diagnosis": "thermal", "action": "set_power_mode", "value": "eco", "reason": "too hot"}'
    d = parse_decision(content)
    assert d == {
        "action": "set_power_mode",
        "value": "eco",
        "diagnosis": "thermal",
        "reason": "too hot",
    }


def test_json_wrapped_in_markdown_fences_parses():
    content = '```json\n{"action": "scale_workers", "value": 4, "diagnosis": "latency", "reason": "slow"}\n```'
    d = parse_decision(content)
    assert d["action"] == "scale_workers"
    assert d["value"] == 4
    assert d["diagnosis"] == "latency"


def test_prose_then_json_extracts_the_object():
    content = (
        "Sure! Based on the telemetry the device is overheating. "
        'Here is my decision: {"action": "set_power_mode", "value": "eco", '
        '"diagnosis": "thermal", "reason": "cpu over threshold"} Hope that helps.'
    )
    d = parse_decision(content)
    assert d["action"] == "set_power_mode"
    assert d["value"] == "eco"


def test_null_value_action_preserved():
    content = '{"action": "restart_worker", "value": null, "diagnosis": "error", "reason": "errors high"}'
    d = parse_decision(content)
    assert d["action"] == "restart_worker"
    assert d["value"] is None


def test_missing_optional_keys_get_defaults():
    content = '{"action": "scale_workers", "value": 4}'
    d = parse_decision(content)
    assert d["action"] == "scale_workers"
    assert d["diagnosis"] == "unknown"
    assert d["reason"] == ""


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   ",
        "not json at all",
        "{ broken json ",
        "[1, 2, 3]",  # valid JSON, but not an object
        "null",
    ],
)
def test_malformed_or_garbage_falls_back_to_noop(content):
    d = parse_decision(content)
    assert d["action"] == "noop"
    assert d["diagnosis"] == "parse_error"


def test_missing_action_key_falls_back():
    content = '{"diagnosis": "thermal", "value": "eco", "reason": "no action field"}'
    d = parse_decision(content)
    assert d["action"] == "noop"
    assert d["diagnosis"] == "parse_error"


def test_empty_action_falls_back():
    content = '{"action": "", "value": null, "diagnosis": "x", "reason": "y"}'
    d = parse_decision(content)
    assert d["action"] == "noop"


def test_parse_decision_never_raises():
    # A grab-bag of hostile inputs; none should raise.
    for content in [None, "", "}{", '{"action":', "\x00\x01", '{"action": 123}']:
        try:
            d = parse_decision(content)
        except Exception as exc:  # noqa: BLE001 - the whole point is no escape
            pytest.fail(f"parse_decision raised on {content!r}: {exc}")
        assert "action" in d

"""Tests for the evaluation harness (evaluate.py)."""

from agent import MockBrain
from evaluate import run_eval, summarize
from scenarios import SCENARIOS


class StubBrain:
    """A brain that always proposes a fixed named action."""

    def __init__(self, action, value=None):
        self._proposal = {
            "action": action,
            "value": value,
            "diagnosis": "stub",
            "reason": "stub",
        }

    def decide(self, view):
        return dict(self._proposal)


def by_fault(*faults):
    return [s for s in SCENARIOS if s.fault in faults]


def one(name):
    return [s for s in SCENARIOS if s.name == name]


# --------------------------------------------------------------------------- #
# Known-good baseline
# --------------------------------------------------------------------------- #


def test_mockbrain_scores_all_three_true_on_every_scenario():
    results = run_eval(MockBrain())
    assert len(results) == len(SCENARIOS)
    for r in results:
        assert r["diagnosed"] is True
        assert r["in_bounds"] is True
        assert r["resolved"] is True


# --------------------------------------------------------------------------- #
# Wrong family but valid -> diagnosed False, in_bounds True
# --------------------------------------------------------------------------- #


def test_wrong_family_brain_misdiagnoses_but_stays_in_bounds():
    # scale_workers is the wrong family for thermal and error faults.
    brain = StubBrain("scale_workers", 4)
    results = run_eval(brain, scenarios=by_fault("thermal", "error"))
    assert results  # non-empty
    for r in results:
        assert r["diagnosed"] is False
        assert r["in_bounds"] is True  # valid action, just the wrong one


# --------------------------------------------------------------------------- #
# Out of bounds -> in_bounds False
# --------------------------------------------------------------------------- #


def test_out_of_bounds_brain_scores_in_bounds_false():
    brain = StubBrain("scale_workers", 99)  # above WORKER_COUNT_RANGE
    results = run_eval(brain)
    for r in results:
        assert r["in_bounds"] is False


# --------------------------------------------------------------------------- #
# Independence: right diagnosis, under-fix -> diagnosed True, resolved False
# --------------------------------------------------------------------------- #


def test_correct_diagnosis_but_underfix_is_independent_of_resolved():
    # Correct family for latency (scale_workers), but worker_count=2 is the
    # device's current value and does not drain the backlog -> not resolved.
    brain = StubBrain("scale_workers", 2)
    results = run_eval(brain, scenarios=one("latency_basic"))
    r = results[0]
    assert r["diagnosed"] is True       # right family
    assert r["in_bounds"] is True       # valid action
    assert r["resolved"] is False       # but it didn't restore health
    assert r["result"] == "rolled_back"  # loop undid the useless change


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def test_summarize_aggregates_rates():
    results = [
        {"diagnosed": True, "in_bounds": True, "resolved": True},
        {"diagnosed": True, "in_bounds": True, "resolved": False},
        {"diagnosed": False, "in_bounds": True, "resolved": False},
        {"diagnosed": False, "in_bounds": False, "resolved": False},
    ]
    s = summarize(results)
    assert s["total"] == 4
    assert s["diagnosed"] == 2 and s["diagnosed_rate"] == 0.5
    assert s["in_bounds"] == 3 and s["in_bounds_rate"] == 0.75
    assert s["resolved"] == 1 and s["resolved_rate"] == 0.25

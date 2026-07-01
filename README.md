# Edge Agent Ops
Bounded agentic operations for a fleet of edge AI inference nodes. An LLM agent
will diagnose faults and remediate them within hard policy limits, with approval,
audit, verification, and rollback. (In active development.)

## Status
- [x] Fleet simulator: devices, deterministic telemetry, fault model
- [x] Guardrail engine (policy + bounds + approval + audit)
- [x] Agent loop (observe → decide → check → apply → verify → rollback)
- [x] Evaluation harness
- [x] Local model (Ollama)
- [x] Dashboard (replay visualization)

## The world (today)
Brief: devices have a controllable parameter surface, telemetry that's a pure
function of params+fault, and a hidden fault the agent must infer. Health is
defined by three thresholds. See fleet.py.

## Guardrails
Every action is gated by a pure policy check returning ALLOW / NEEDS_APPROVAL / DENY.
Default-deny whitelist; bounds imported from fleet.py; high-risk actions require
approval; all decisions are recorded in an append-only audit log. See guardrails.py.

## Agent loop
The closed remediation loop ties the world and the policy gate together: observe a
device, ask a brain to diagnose and propose a named action, gate it through
guardrails.evaluate, apply it only if permitted, verify the device recovered, and roll
back to the pre-action snapshot if it didn't. The brain is a rules-based mock for now;
the real local model drops into this same loop later. Every action passes through
policy by construction, and every phase is recorded to the audit log. Outcomes are
honest: success / rolled_back / denied / no_diagnosis. See agent.py.

## Evaluation
The harness scores any brain against a battery of known-fault scenarios on three
independent dimensions — diagnosed (correct action family), in_bounds (passes policy),
and resolved (device actually healthy after the real remediate loop runs). Ground truth
is visible to the scorer but never to the brain under test. The MockBrain scores 6/6 as
the known-good baseline; the same harness will score the real local model unchanged.
See evaluate.py and scenarios.py.

## Local model
The brain is now a real local LLM served by Ollama, behind the same decide(view)
interface the mock used — selected with `--backend ollama --model <name>`. Inference is
deterministic (temp 0, reasoning off) and fails safe: any malformed response degrades to
"no action taken" rather than crashing or acting wrongly. The same eval harness scores it
unchanged; both qwen3:8b and qwen3:14b resolve all six scenarios, with 8b faster on
24GB-class hardware. See llm_brain.py.

## Dashboard
A single self-contained page (dashboard.html) that replays a real recorded run of the
system — fleet health tiles, a live decision stream, and the eval scorecard — with a
play/scrub timeline. It foregrounds the guardrails in action: a denied out-of-bounds
action, a rollback, and an approval hold. Runs from file:// with no backend; the data
comes from export_run.py. [Live demo coming soon].

## Run
python fleet.py     # demo: inject a fault, apply the fix, watch health restore
pytest -v           # tests
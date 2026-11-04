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
- [x] Observability (Prometheus + Grafana)

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
honest: success / rolled_back / denied / declined / no_diagnosis. See agent.py.

## Evaluation
The harness scores any brain against a battery of known-fault scenarios on three
independent dimensions — diagnosed (correct action family), in_bounds (passes policy),
and resolved (device actually healthy after the real remediate loop runs). Ground truth
is visible to the scorer but never to the brain under test. The MockBrain scores 6/6 as
the known-good baseline; the same harness will score the real local model unchanged.
See evaluate.py and scenarios.py.

## Local model
The brain is now a real local LLM served by Ollama, behind the same decide(view)
interface the mock used — selected with `--backend ollama --model <name>`. Requests use
temperature 0, JSON-only output, and `"think": false`, and the brain fails safe: any
malformed response degrades to "no action taken" rather than crashing or acting wrongly.
The same eval harness scores it unchanged. See llm_brain.py.

| Model | Thinking | Per decision | Diagnosed | In bounds | Resolved |
|---|---|---|---|---|---|
| qwen3:8b | on | ~12.1 s | 6/6 | 6/6 | 6/6 |
| qwen3:8b | off | ~1.5 s | 6/6 | 6/6 | 6/6 |
| qwen3:14b | off | ~2.7 s | 6/6 | 6/6 | 6/6 |

Measured on a single 24 GB GPU, one model at a time after a warm-up request. Thinking
mode cost about 8x in latency and changed no score — on these single-fault scenarios.
See "Limitations and next steps" for why that may not hold on harder ones.

## Dashboard
A single self-contained page (dashboard.html) that replays a real recorded run of the
system — fleet health tiles, a live decision stream, and the eval scorecard — with a
play/scrub timeline. It foregrounds the guardrails in action: a denied out-of-bounds
action, a rollback, and an approval hold. The eval panel compares all three model
configurations above side by side. Runs from file:// with no backend; the data comes from
export_run.py. [Live demo](https://aakashagrawal.com/projects/edgeops/dashboard.html).

## Observability
Optional local ops stack. The agent is instrumented with Prometheus metrics (metrics.py) — remediation outcomes by result, decision-latency histogram, approval holds, fleet health, and eval scores. serve_live.py exposes them on :8000; a docker-compose stack brings up Prometheus + a pre-provisioned Grafana board showing the guardrail story — denial rate, rollback rate, approval holds, fleet health, and decision-latency percentiles. This is real instrumentation, distinct from the replay dashboard. See OBSERVABILITY.md to run it.

## Limitations and next steps
This is a working prototype. The gaps that matter most:

- **Single-fault scenarios.** Each scenario has one fault with one canonical fix, and the
  telemetry points straight at it. That's why thinking mode added nothing: there was
  little to reason about. The current eval can't say whether reasoning helps on harder cases.
- **One action per attempt.** The loop applies one action, then verifies full health. On a
  device with two faults, any single correct fix would still fail verification and be
  rolled back. The simulator holds one fault per device today, so this is a design limit
  rather than an observed failure. Compound faults need a loop that can make progress over
  several steps, for example by accepting a change that strictly improves health.
- **No retry with feedback.** A rolled-back fix ends the attempt; the brain never sees why
  it failed.
- **Auto-approval in batch mode.** Non-interactive runs grant every approval. In production
  that has to be a human or a second system.
- **Append-only, not tamper-evident, audit log**, and a **stateless policy** with no rate
  limits or per-device history.

Planned experiments:

1. **Harder scenarios:** thermal faults at high batch size (low-power mode alone isn't
   enough), two faults on one device, and telemetry that points at the wrong cause.
2. **Thinking as a setting:** expose it as a flag so the eval can run both ways and measure
   what reasoning buys against its ~8x latency cost.
3. **Escalation:** answer with thinking off, and only after a rollback retry with thinking
   on and the failed attempt as context.

## Run
python fleet.py     # demo: inject a fault, apply the fix, watch health restore
pytest -v           # tests

python evaluate.py --backend ollama --model qwen3:8b    # score a real model
python export_run.py --backend ollama --model qwen3:8b  # record a run for the dashboard
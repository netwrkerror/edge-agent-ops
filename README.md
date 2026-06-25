# Edge Agent Ops
Bounded agentic operations for a fleet of edge AI inference nodes. An LLM agent
will diagnose faults and remediate them within hard policy limits, with approval,
audit, verification, and rollback. (In active development.)

## Status
- [x] Fleet simulator: devices, deterministic telemetry, fault model
- [x] Guardrail engine (policy + bounds + approval + audit)
- [ ] Agent loop (observe → decide → check → apply → verify → rollback)
- [ ] Evaluation harness
- [ ] Local model (Ollama)

## The world (today)
Brief: devices have a controllable parameter surface, telemetry that's a pure
function of params+fault, and a hidden fault the agent must infer. Health is
defined by three thresholds. See fleet.py.

## Guardrails
Every action is gated by a pure policy check returning ALLOW / NEEDS_APPROVAL / DENY.
Default-deny whitelist; bounds imported from fleet.py; high-risk actions require
approval; all decisions are recorded in an append-only audit log. See guardrails.py.

## Run
python fleet.py     # demo: inject a fault, apply the fix, watch health restore
pytest -v           # tests
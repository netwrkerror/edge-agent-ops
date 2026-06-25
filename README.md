# Edge Agent Ops
Bounded agentic operations for a fleet of edge AI inference nodes. An LLM agent
will diagnose faults and remediate them within hard policy limits, with approval,
audit, verification, and rollback. (In active development.)

## Status
- [x] Fleet simulator: devices, deterministic telemetry, fault model
- [ ] Guardrail engine (policy + bounds + approval + audit)
- [ ] Agent loop (observe → decide → check → apply → verify → rollback)
- [ ] Evaluation harness
- [ ] Local model (Ollama)

## The world (today)
Brief: devices have a controllable parameter surface, telemetry that's a pure
function of params+fault, and a hidden fault the agent must infer. Health is
defined by three thresholds. See fleet.py.

## Run
python fleet.py     # demo: inject a fault, apply the fix, watch health restore
pytest -v           # tests
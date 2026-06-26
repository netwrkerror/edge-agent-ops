# CLAUDE.md — project context

## What this is
A local, private-AI system (in progress): an LLM agent will monitor a fleet of edge AI
inference nodes, diagnose faults, and remediate them ONLY within hard policy limits,
with approval, audit, verification, and rollback. The product is the guardrail +
evaluation harness, not the agent.

## Status
Building from scratch, step by step. Completed step: Step 1 — the fleet simulator. Current step: Step 2 — guardrails.

## Modules
- fleet.py — the simulated world. Devices with Params (action surface),
  Telemetry (pure function of params+fault, read-only), and a hidden fault.
  Health thresholds and param ranges are defined here and are the single source
  of truth. view() exposes telemetry only; the fault stays hidden (the agent must
  infer it). snapshot()/restore() support rollback. REMEDIATION/remediation_for
  encode ground-truth fixes — for the world/eval ONLY; the agent under test must
  never import them or read .fault.
  
- guardrails.py — the policy gate. evaluate(action, value) is a PURE function
  returning ALLOW/NEEDS_APPROVAL/DENY. Default-deny whitelist of NAMED actions;
  bounds imported from fleet.py (never hardcoded). Approval workflow for high-risk
  actions; append-only AuditLog.

- agent.py — the closed remediation loop. The ONLY module that translates named
  actions into fleet's apply() schema and the ONLY caller of guardrails.evaluate.
  remediate() runs snapshot → decide → gate → apply → verify → rollback; sweep()
  runs it over every unhealthy device. MockBrain proposes actions from telemetry
  only (never the hidden fault). The real LLM brain will implement the same
  decide(view)->named-action interface and drop in unchanged.

- scenarios.py — evaluation scenarios: each builds a fresh faulted device and declares
  the ground-truth fault. Params kept in solvable ranges (single canonical fix suffices).

- evaluate.py — scores any brain (decide(view) interface) on three INDEPENDENT
  dimensions: diagnosed (by action family, not exact value), in_bounds (non-DENY), and
  resolved (healthy after the real agent.remediate loop). The scorer may read ground
  truth; the brain under test sees only device.view().

## Invariants (additions)
- No action reaches the world without passing guardrails.evaluate first.
- evaluate() is pure: no mutation, no I/O. Anything not whitelisted is DENIED.
- Bounds come from fleet.py; the policy layer imports, never redefines them.
- The agent never applies an action without an ALLOW/approved verdict; translation
  to the world schema happens only after the gate.
- Every remediation snapshots first and rolls back if health isn't restored.
- The agent is the sole writer to the AuditLog; evaluate() stays pure.
- Four honest outcomes — success / rolled_back / denied / no_diagnosis — kept
  distinct so the audit trail tells the truth about why nothing changed.
- Eval scores three dimensions independently; never collapse them into one pass/fail.
- diagnosed is matched by action family, never exact value.
- resolved must run the real remediate loop, not a re-implementation.
- Only the scorer reads ground truth (.fault / remediation_for); the brain never does.

## Conventions
- Python 3.9+, standard library only for the core.
- Small, single-responsibility modules; clear names; pure functions for policy checks.
- After each step, the code must run, and we commit before moving on.
- Every module ships with pytest tests that prove its contract/invariants.
  Test behavior and boundaries, not trivial getters.
- One action vocabulary at the policy boundary: named actions
  (set_power_mode/set_batch_size/scale_workers/restart_worker). The agent
  translates these to fleet's apply() schema; the world is not given action names.
- Bounds are defined once in fleet.py and imported by the policy layer.
- Every module ships with pytest tests proving its contract; test behavior and
  boundaries, not trivial getters.

## How to work
Do ONE step at a time. Don't build ahead. Don't create modules that haven't been asked for.
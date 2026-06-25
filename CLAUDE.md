# CLAUDE.md — project context

## What this is
A local, private-AI system (in progress): an LLM agent will monitor a fleet of edge AI
inference nodes, diagnose faults, and remediate them ONLY within hard policy limits,
with approval, audit, verification, and rollback. The product is the guardrail +
evaluation harness, not the agent.

## Status
Building from scratch, step by step. Current step: Step 1 — the fleet simulator.

## Modules
- fleet.py — the simulated world. Devices with Params (action surface),
  Telemetry (pure function of params+fault, read-only), and a hidden fault.
  Health thresholds and param ranges are defined here and are the single source
  of truth. view() exposes telemetry only; the fault stays hidden (the agent must
  infer it). snapshot()/restore() support rollback. REMEDIATION/remediation_for
  encode ground-truth fixes — for the world/eval ONLY; the agent under test must
  never import them or read .fault.

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
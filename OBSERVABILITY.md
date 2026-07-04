# Observability

Live Prometheus + Grafana stack that scrapes the agent's `/metrics` and shows the
guardrail story (denials, rollbacks, approval holds) on a pre-built dashboard.
Everything is provisioned as code — no click-configuring.

## Layout

- `serve_live.py` — runs on the **host**, exposes `/metrics` on `:8000`. It loops
  remediation sweeps with rotating outcomes so all series have data.
- `prometheus.yml` — scrapes `host.docker.internal:8000` every 5s (job `edgeops`).
- `docker-compose.yml` — Prometheus (`:9090`) + Grafana (`:3000`), Grafana
  auto-provisioned with the datasource and dashboard.
- `grafana/dashboards/edgeops.json` — the dashboard, loaded at startup.

## Run it

**Terminal 1 — the metrics source (on the host):**

```
# uses the project venv where prometheus_client is installed
./.venv/bin/python serve_live.py
# -> serving /metrics on :8000 (backend=mock, model=mock)
```

**Terminal 2 — the observability stack:**

```
docker compose up
```

**Then open Grafana:** http://localhost:3000

The dashboard **edge agent ops — guardrails** loads automatically (dark theme,
anonymous admin — no login). Prometheus UI is at http://localhost:9090 if you want
to check the target is `UP` (Status → Targets).

Stop with `ctrl-c` in each terminal; `docker compose down` removes the containers.

## Panels

- **fleet healthy** — `edgeops_fleet_healthy`; sawtooth that dips while a fault is
  active and recovers after remediation.
- **remediation outcomes over time** — `rate(edgeops_remediations_total[1m])` by
  result (success / denied / rolled_back / declined / no_diagnosis).
- **denial + rollback rate** — the guardrail story: policy-blocked actions and
  fixes that failed verification and were rolled back.
- **approval holds** — `rate(edgeops_approval_holds_total[1m])`: high-risk actions
  held for a human.
- **decision latency (p50 / p95)** — `histogram_quantile` over
  `edgeops_decision_latency_seconds_bucket`; tiny for the mock brain, seconds for a
  local LLM (`serve_live.py --backend ollama --model qwen3:8b`).
- **eval scores** — `edgeops_eval_score` by dimension; static with the mock brain.

## Notes

- **Host networking:** on Docker Desktop (Mac/Windows) containers reach the host's
  `:8000` via `host.docker.internal` (used in `prometheus.yml`) — not `localhost`,
  which would resolve to the container itself. The compose file adds a
  `host-gateway` alias so this also works on Linux.
- If a panel says "No data", confirm `serve_live.py` is running and the Prometheus
  target is `UP`; give it ~15s for the first scrapes and rate windows to fill.

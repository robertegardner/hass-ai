# hass-ai — Predictive Automation Engine (PAE)

Observes Home Assistant activity, statistically mines behavioral patterns per person and
context, uses a local LLM (Ollama) to filter/frame/generate automation proposals, and
graduates automations through three trust levels: **shadow → suggest → autonomous**.
The LLM never sits in the real-time control loop; HA's native automation engine executes
everything. Predictability beats cleverness.

**Current status: Phase 1** — event ingestion & schema. PAE still writes nothing to Home
Assistant (enforced in code: outbound WebSocket message whitelist — read-only commands
only — plus a `ReadOnlyViolation` guard on all write-shaped methods).

## Stack

- `api` — FastAPI (`/healthz`, `/readyz`, `/metrics`), port 8000
- `ingester` — independent HA WebSocket listener (does not touch HA's recorder):
  behavioral events with `triggered_by` attribution, presence snapshots, per-minute
  context frames, daily entity-registry mirror. Metrics on internal :9100
- `worker` — RQ worker on queue `pae:default`, Prometheus metrics on internal :9100
- `db` — TimescaleDB (Postgres 16): hypertables `events` (12-month retention),
  `presence_snapshots`, `context_frames`; `events_hourly` continuous aggregate;
  `entity_registry` mirror. Migrations via Alembic (`pae migrate`, auto-run by ingester)
- `redis` — job queue backend

## Attribution (`triggered_by`)

Every ingested event is classified `manual` / `automation` / `pae` using HA's context
parent chain: the ingester caches context ids announced by `automation_triggered` /
`script_started` and matches each `state_changed` context id or parent id against the
cache; an unseen parent id still counts as `automation` (never credit a non-human chain
as manual). Physical actions and UI/app actions are both `manual`. Logic in
`src/pae/ingest/attribution.py`.

## Grafana

`grafana/pae-dashboard-1-ingestion.json` — import into Grafana (192.168.6.51) with a
PostgreSQL datasource pointing at the PAE database. Panels: event volume by domain,
manual-vs-automation ratio, presence timeline, room-level detections, context frames.

## Quickstart

```bash
cp .env.example .env      # then fill in HA_TOKEN (never commit .env)
docker compose up -d
docker compose ps         # all services healthy
curl localhost:8000/healthz
```

## Smoke test (read-only, against live HA)

```bash
docker compose run --rm api pae smoke            # or --duration 30
# or from a host venv:
uv sync && uv run pae smoke
# or: uv run python scripts/smoke_test.py
```

Prints HA version, entity count, top domains, then subscribes to `state_changed`
for 60 s and reports the event rate.

## Development

```bash
uv sync                   # deps + dev tools into .venv
uv run pytest             # unit tests (no live HA needed)
uv run ruff check src tests scripts
```

Layout: `src/pae/` — `config.py` (pydantic-settings), `logging.py` (structlog JSON for
Loki), `metrics.py` (Prometheus), `ha/` (WebSocket + REST client, reconnect/backoff,
read-only guards), `api/`, `worker/`, `cli.py` (`pae api|worker|smoke`). Later phases add
`db/`, `ingest/`, `miner/`, `llm/`, `scoring/`.

## Configuration

All via environment / `.env` — see `.env.example`. Key vars: `HA_URL`, `HA_TOKEN`,
`OLLAMA_PRIMARY`, `OLLAMA_FALLBACK`, `DB_URL`, `REDIS_URL`, `LOG_LEVEL`, `LOG_FORMAT`,
`PAE_READ_ONLY` (default `true`).

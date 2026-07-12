# hass-ai — Predictive Automation Engine (PAE)

Observes Home Assistant activity, statistically mines behavioral patterns per person and
context, uses a local LLM (Ollama) to filter/frame/generate automation proposals, and
graduates automations through three trust levels: **shadow → suggest → autonomous**.
The LLM never sits in the real-time control loop; HA's native automation engine executes
everything. Predictability beats cleverness.

**Current status: Phase 2** — statistical pattern mining. PAE still writes nothing to Home
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

## Pattern mining (Phase 2)

A nightly RQ job (enqueued by the worker at `MINER_RUN_HOUR_UTC`, default 09:00 UTC)
mines the last `MINER_LOOKBACK_DAYS` (60) of events for two pattern kinds:

- **time_of_day** — a manual action recurring at a consistent local time, conditioned on
  weekday/weekend (US holidays count as weekend). Scored by support (active days /
  observed days), temporal consistency (share of occurrences within ±45 min of the
  circular mean), and lift vs. a uniform-over-the-day baseline.
- **event_pair** — a manual action following some other state transition within 5
  minutes. Scored by association-rule support/confidence/lift. Near-simultaneous pairs
  (< 2 s) are discarded as state mirrors.

Clockwork-regular device-originated patterns (circular std ≤ 2 min, no user_id — e.g.
the Pentair pool controller's own schedule) are flagged `suspected_schedule`, kept but
never to be proposed as automations. Patterns upsert into `patterns` by `pattern_key`;
the miner never touches `status` (lifecycle belongs to Phase 3+).

    pae mine             # run the miner once, now
    pae patterns list    # top patterns by lift (--kind, --limit)

## Grafana

- `grafana/pae-dashboard-1-ingestion.json` — event volume by domain, manual-vs-automation
  ratio, presence timeline, room-level detections, context frames.
- `grafana/pae-dashboard-2-patterns.json` — mined patterns: counts by kind, suspected
  external schedules, staleness, top patterns by lift.

Import into Grafana (192.168.6.51) with a PostgreSQL datasource pointing at the PAE
database (Grafana 13: datasource type `grafana-postgresql-datasource`).

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

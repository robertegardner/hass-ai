# hass-ai — Predictive Automation Engine (PAE)

Observes Home Assistant activity, statistically mines behavioral patterns per person and
context, uses a local LLM (Ollama) to filter/frame/generate automation proposals, and
graduates automations through three trust levels: **shadow → suggest → autonomous**.
The LLM never sits in the real-time control loop; HA's native automation engine executes
everything. Predictability beats cleverness.

**Current status: Phase 0** — stack scaffold + read-only HA connectivity. Nothing is ever
written to Home Assistant in this phase (enforced in code: outbound WebSocket message
whitelist + `ReadOnlyViolation` guard on all write-shaped methods).

## Stack

- `api` — FastAPI (`/healthz`, `/readyz`, `/metrics`), port 8000
- `worker` — RQ worker on queue `pae:default`, Prometheus metrics on internal :9100
- `db` — TimescaleDB (Postgres 16); schema arrives in Phase 1
- `redis` — job queue backend

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

# PAE (Predictive Automation Engine)

Observes Home Assistant, mines behavioral patterns, graduates automations shadow → suggest
→ autonomous. Full spec and phase plan: see README and `.claude/plans/`. **Phases are
gated: stop at each phase end, present results, wait for explicit operator approval.**

## Operator hard rules

- Show complete rewritten files, never diffs, when presenting file changes in chat.
- Confirm before ANY write to Home Assistant (automations/helpers/labels), DB table drops,
  or HA config changes. Reads are fine.
- Ask before installing system-level dependencies on shared hosts.
- The ADS-B Pi on the network is permanently off-limits.
- Bias precision over recall everywhere: a wrong autonomous action costs more than a missed one.

## Commands

- `uv run pytest` — tests (live-HA tests excluded by default via `-m 'not live'`)
- `uv run ruff check src tests scripts` — lint (line length 100)
- `sudo docker compose up -d --build <svc>` — deploy (user in docker group; sudo needed until re-login)
- `pae api|worker|ingest|migrate|smoke` — CLI entry points (`src/pae/cli.py`)
- Alembic migrations in `src/pae/db/migrations/`; ingester runs `migrate` on start

## Architecture invariants

- **Read-only enforcement**: `ALLOWED_OUTBOUND_TYPES` whitelist in `src/pae/ha/client.py`
  gates every outbound WS frame; write-shaped methods carry `@writes_to_ha` and raise
  `ReadOnlyViolation`. Widening the whitelist is a reviewed, per-phase act.
- Attribution logic (`src/pae/ingest/attribution.py`) is the correctness core: unseen
  parent context → `automation`, never `manual`. Keep unit tests exhaustive.
- LLM (Ollama-only, no cloud) never executes directly; structured JSON validated against
  schema + entity registry before anything touches HA (Phase 3+).

## Environment facts

- HA: `http://homeassistant.iot:8123`, token in gitignored `.env` (`HA_TOKEN`)
- Ollama: 192.168.85.61:11434 (5090, primary), 192.168.6.164:11434 (3080, fallback)
- Grafana 13 at 192.168.6.51:3000; dashboard uid `pae-ingestion`; datasource "PAE Postgres"
  (uid `afreiil0pq41se`) → this host (192.168.6.218:5432) via SELECT-only `grafana_ro` role
  (password: `GRAFANA_DB_PASSWORD` in `.env`)
- Dev host runs the full stack (docker compose); db bound to LAN :5432 for Grafana

## Gotchas

- `sensor.weewx_outisde_temperature` — the HA entity really is misspelled; use verbatim.
- UniFi camera friendly names are authoritative for rooms; entity_ids are stale from renames.
- HA WS needs `max_msg_size=0` (registry frames > aiohttp's 4MB default on this install).
- Chunk bulk inserts (Postgres 65,535 bind-param limit; ~11k registry entities).
- Grafana 13 datasource refs need type `grafana-postgresql-datasource`, not `postgres`.
- Tests use a fake HA WS server (`tests/conftest.py`); import from `conftest`, not `tests.conftest`.

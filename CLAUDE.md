# PAE (Predictive Automation Engine)

Observes Home Assistant, mines behavioral patterns, graduates automations shadow → suggest
→ autonomous. Full spec and phase plan: see README and `docs/superpowers/plans/`. **Phases are
gated: stop at each phase end, present results, wait for explicit operator approval.**
Phases 0–2 accepted; Phase 3 (LLM proposals) not started.

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
- `pae api|worker|ingest|migrate|smoke|mine` — CLI entry points (`src/pae/cli.py`)
- `pae patterns list [--kind time_of_day|event_pair] [--limit N]` — inspect mined patterns
- Alembic migrations in `src/pae/db/migrations/`; ingester runs `migrate` on start
- `uv run --group weather python ha/weather/<script>.py` — weather stack tooling
  (discovery/helpers/build_dashboard/deploy_dashboard; order + dry-runs in `ha/weather/README.md`)

## Architecture invariants

- **Read-only enforcement**: `ALLOWED_OUTBOUND_TYPES` whitelist in `src/pae/ha/client.py`
  gates every outbound WS frame; write-shaped methods carry `@writes_to_ha` and raise
  `ReadOnlyViolation`. Widening the whitelist is a reviewed, per-phase act.
- Attribution logic (`src/pae/ingest/attribution.py`) is the correctness core: unseen
  parent context → `automation`, never `manual`. Keep unit tests exhaustive.
- The miner NEVER touches `patterns.status` — that column is the Phase 3+ lifecycle.
  Upserts key on `pattern_key`; `reconcile_keys` keeps tod keys stable across runs.
- `suspected_schedule` patterns (clockwork std ≤2 min, no user_id) are external
  controllers (Pentair etc.) — never propose them as automations. Pairs have no such
  flag: Phase 3 must also exclude pairs whose entities have sched-flagged tod siblings,
  and gate promotion on `temporal_consistency`/`tod_std_minutes`, not just support/lift.
- LLM (Ollama-only, no cloud) never executes directly; structured JSON validated against
  schema + entity registry before anything touches HA (Phase 3+).
- `ha/weather/` is standalone from PAE on purpose (PAE's HA client is read-only enforced)
  and owns ALL `sdr/davis` HA discovery; the sdr-fleet bridge publishes states only —
  re-adding discovery there creates duplicate entities.

## Environment facts

- HA: `http://homeassistant.iot:8123`, token in gitignored `.env` (`HA_TOKEN`); HA
  timezone is America/Chicago (miner `miner_local_tz` default matches)
- Ollama: 192.168.85.61:11434 (5090, primary), 192.168.6.164:11434 (3080, fallback)
- Grafana 13 at 192.168.6.51:3000; dashboards `pae-ingestion` and `pae-patterns`;
  datasource "PAE Postgres" (uid `afreiil0pq41se`) → this host (192.168.6.218:5432) via
  SELECT-only `grafana_ro` role (schema-wide — new tables readable without extra grants;
  password: `GRAFANA_DB_PASSWORD` in `.env`)
- Dev host runs the full stack (docker compose); db bound to LAN :5432 for Grafana
- Nightly mine job: worker scheduler thread, `MINER_RUN_HOUR_UTC` (default 9 = 3/4am CT)

## Gotchas

- `sensor.weewx_outisde_temperature` — the HA entity really is misspelled; use verbatim.
- UniFi camera friendly names are authoritative for rooms; entity_ids are stale from renames.
- HA WS needs `max_msg_size=0` (registry frames > aiohttp's 4MB default on this install).
- Chunk bulk inserts (Postgres 65,535 bind-param limit; ~11k registry entities).
- Grafana 13 datasource refs need type `grafana-postgresql-datasource`, not `postgres`.
- Tests use a fake HA WS server (`tests/conftest.py`); import from `conftest`, not `tests.conftest`.
- Worker must stay `rq.SimpleWorker` (in-process): the default forking `Worker` loses
  `pae_miner_*` metrics from the registry served on :9100.
- External device schedules (Pentair `switch.pool_2`, the dusk-to-dawn switch cluster)
  emit `manual` events — they dominate naive pattern lift; see miner invariants.
- MQTT discovery payloads must set `object_id`, or HA derives entity ids from
  area+device+name (`binary_sensor.outside_davis_vantage_pro2_plus_...`).
- The weather dashboard is generated — edit `build_dashboard.py`, rebuild, redeploy;
  never hand-edit it in HA.
- Davis bridge code lives in `~/projects/sdr-fleet/profiles/davis/`, runs on `sdr-indoor`
  (via thebeast jump `deploy@192.168.6.163` → `root@192.168.6.90`);
  `deploy-agent.sh sdr-indoor` does NOT restart `sdr@DAVIS1.service` — restart manually.
- The Davis ISS can brownout-reboot onto another TX id (2026-07-13, weak battery);
  `davis-watchdog.timer` on sdr-indoor auto-retunes `DAVIS_TR`. Battery is a low-bit
  only (byte 0 bit 3) — no voltage OTA.
- paho-mqtt: subscribe inside `on_connect` — a subscribe right after `connect()` is
  silently lost.

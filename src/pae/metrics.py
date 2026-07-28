import platform

from prometheus_client import Counter, Gauge, Info

from pae import __version__

BUILD_INFO = Info("pae_build", "PAE build information")
BUILD_INFO.info({"version": __version__, "python_version": platform.python_version()})

HA_WS_CONNECTED = Gauge(
    "pae_ha_ws_connected", "1 if the Home Assistant WebSocket is connected and authenticated"
)
HA_WS_RECONNECTS = Counter(
    "pae_ha_ws_reconnects_total", "Number of Home Assistant WebSocket reconnect attempts"
)
HA_EVENTS_RECEIVED = Counter(
    "pae_ha_events_received_total",
    "Events received over the Home Assistant WebSocket",
    ["event_type"],
)

INGEST_ROWS_WRITTEN = Counter(
    "pae_ingest_rows_written_total", "Rows written to the database", ["table"]
)
INGEST_EVENTS_FILTERED = Counter(
    "pae_ingest_events_filtered_total", "state_changed events dropped by the domain filter"
)
INGEST_EVENTS_ATTRIBUTED = Counter(
    "pae_ingest_events_attributed_total", "Ingested events by attribution", ["triggered_by"]
)
INGEST_FLUSH_ERRORS = Counter(
    "pae_ingest_flush_errors_total", "Database flush failures (rows retried on next flush)"
)

MINER_RUNS = Counter("pae_miner_runs_total", "Pattern-mining runs", ["status"])
MINER_RUN_SECONDS = Gauge("pae_miner_run_seconds", "Duration of the last mining run")
MINER_LAST_SUCCESS = Gauge(
    "pae_miner_last_success_timestamp", "Unix time of the last successful mining run"
)
MINER_PATTERNS = Gauge(
    "pae_miner_patterns", "Patterns currently in the patterns table", ["kind"]
)

LLM_REQUESTS = Counter("pae_llm_requests_total", "LLM chat requests", ["model", "status"])
LLM_SECONDS = Gauge("pae_llm_last_duration_seconds", "Duration of the last LLM request")
PROPOSALS_BY_STATUS = Gauge("pae_proposals", "Proposals by status", ["status"])
PROPOSER_VALIDATION_FAILURES = Counter(
    "pae_proposer_validation_failures_total",
    "LLM outputs rejected by schema/registry validation (after one retry)",
)
SHADOW_DAYS_SCORED = Counter(
    "pae_shadow_days_scored_total", "Proposal-days scored by the shadow evaluator"
)

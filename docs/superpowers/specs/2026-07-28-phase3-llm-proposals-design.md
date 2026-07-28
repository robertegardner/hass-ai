# PAE Phase 3 — LLM Automation Proposals + Shadow Evaluation — Design

**Date:** 2026-07-28
**Status:** Approved by operator (design); implementation not started

## Purpose

Turn mined behavioral patterns into reviewable Home Assistant automation proposals. A
local LLM (Ollama-only) authors each proposal's automation JSON; deterministic code
gates what the LLM sees and validates everything it produces. A nightly shadow
evaluator replays each proposal against real events to score how well it predicts the
human, and a web UI on the existing `api` service is the review surface.

Phase 3 writes **nothing** to Home Assistant. `ALLOWED_OUTBOUND_TYPES` in
`src/pae/ha/client.py` is untouched; verifying that is part of acceptance. Suggest-mode
(writing approved automations into HA) is Phase 4.

## Scope

In scope: `proposals` + `shadow_results` tables (migration 0003), eligibility gates and
sibling-aware grouping, Ollama client with structured output + failover, prompt v1,
automation-subset schema validation, nightly generate + shadow-eval jobs chained after
the miner, proposal lifecycle (including `patterns.status` ownership), server-rendered
review UI, Prometheus metrics, offline test suite.

Out of scope: any HA write, UI auth (Phase 4, when writes become possible), Grafana
dashboard #3, notifications, proposal editing in the UI, LLM-proposed refinements
beyond the constrained subset, event_pair sun anchoring.

## Settled decisions

- Proposals + shadow eval only; zero HA writes (operator, 2026-07-28)
- The LLM authors the full automation JSON, schema + registry validated (operator)
- Review surface is a web UI on the `api` service (operator)
- Nightly batch generation with deterministic pre-grouping; one LLM call per group
  (operator)
- Precision over recall everywhere: a skipped good proposal is fine; a bad proposal
  surviving validation is the failure mode that matters.

## Data model (migration 0003)

`proposals`:

| column | type | notes |
|---|---|---|
| id | bigint identity PK | |
| group_key | text unique | deterministic; stable across regenerations |
| kind | text | `time_of_day` \| `event_pair` |
| title | text | LLM |
| rationale | text | LLM |
| automation_json | jsonb | validated HA automation config subset |
| source_pattern_keys | jsonb | pattern_key list |
| entity_ids | jsonb | all entities referenced |
| model_name | text | e.g. `alibayram/Qwen3-30B-A3B-Instruct-2507` |
| prompt_version | int | 1 |
| status | text | `shadowing` \| `approved` \| `rejected` \| `stale` |
| reject_reason | text nullable | operator note |
| last_eligible_at | timestamptz | stamped each night the source group passes gates; drives staleness |
| created_at / updated_at | timestamptz | |

`shadow_results` (unique `(proposal_id, day)`):

| column | type | notes |
|---|---|---|
| proposal_id | bigint FK | |
| day | date | local (miner tz) |
| expected_fires | int | times the automation would have fired |
| human_matches | int | manual action events inside the window |
| human_total | int | all manual action events that day |

`group_key` construction: `tod:{day_type}:{30-min bucket of group mean}:{sha1 of
sorted entity_ids+action}` / `pair:{trigger_entity}:{trigger_state}:{sha1 of sorted
action entity_ids+action}`.

## Lifecycle

- Nightly generation creates proposals directly in `shadowing` — evaluation starts
  immediately; operator review is not a precondition for measuring.
- Operator: `shadowing → approved` or `shadowing → rejected` (terminal; suppresses
  regeneration of that `group_key`). Approve/reject are PAE-db flags only.
- `stale`: `last_eligible_at` more than 7 days old (source patterns absent from
  mining or below gates that whole time). Stale proposals stay visible, flagged;
  their patterns revert to `candidate`.
- `patterns.status` (Phase 3 owns it; the miner still never touches it):
  `candidate → proposed` when referenced by a live proposal; `→ rejected` when its
  proposal is rejected; `→ candidate` again if its proposal goes stale.

## Eligibility gates (deterministic, before any LLM call)

Never eligible: `suspected_schedule` patterns; any pattern (either kind) whose
entities — for pairs, trigger **or** action entity — have a sched-flagged
time_of_day sibling; entities missing from the registry mirror.

time_of_day: `temporal_consistency ≥ 0.8`, `tod_std_minutes ≤ 30`, `support ≥ 0.6`,
`occurrences ≥ 8`, `days_observed ≥ 14`.

event_pair: `confidence ≥ 0.7`, `lift ≥ 5`, `occurrences ≥ 8`.

All thresholds are settings (`PROPOSER_*`), defaults as above, precision-biased.

## Grouping (deterministic, pre-LLM)

- tod: same day_type, cluster means within 20 min of each other → one group (the
  seven `07:12` switches become one "morning lights" proposal).
- pair: same (trigger_entity, trigger_state) → one group.

## LLM contract

Client in `src/pae/llm/` (sync; runs inside the RQ worker): Ollama `/api/chat` with
`format: <json schema>` (constrained decoding). Primary 192.168.85.61:11434 (5090),
fallback 192.168.6.164:11434 (3080); one retry each, then the group is skipped for the
night. Settings `LLM_MODEL_PRIMARY` (default `alibayram/Qwen3-30B-A3B-Instruct-2507`)
and `LLM_MODEL_FALLBACK` (default `qwen3.5:9b`). The client **refuses model names
containing `:cloud`** — the Ollama-cloud models present on both hosts violate the
no-cloud rule; enforced in code.

Prompt v1 (template in repo, `prompt_version` stamped on the proposal): system prompt
frames the job — "draft a Home Assistant automation from an observed human habit;
decline if it is not sensible" — with the group's pattern stats, sample occurrence
times, and registry context per entity (friendly name, area, domain, device class).

Response schema: `{propose: bool, decline_reason?, title, rationale, automation}`.
`propose:false` is a valid outcome — the LLM doubles as a plausibility filter.

`automation` is a constrained subset of HA automation config:
- `trigger`: `time`, `sun` (with offset), or `state` only
- `condition` (optional): `time` / `state` / `sun` only
- `action`: `service` calls only
- no `delay`, `wait_*`, templates, or script references

## Validation (code, post-LLM, pre-storage)

1. Pydantic schema for the subset; unknown keys rejected.
2. Every entity_id must exist in the registry mirror **and** appear in the source
   patterns — the LLM cannot introduce entities.
3. Service domain must match entity domain.
4. Trigger sanity: tod triggers within ±60 min of the group's cluster mean (or
   equivalent sun offset via `SunCalculator`); pair triggers must use the mined
   trigger entity/state.
5. Declarative subset only (re-checked structurally, not by string matching).

One retry with validation errors appended to the prompt; second failure → group
skipped, `pae_proposer_validation_failures` incremented. Failed output is logged,
never stored.

## Shadow evaluator

Nightly, for proposals in `shadowing`/`approved`, interprets `automation_json` itself
(scores what would ship, not the source pattern):

- `time` trigger → one would-fire on days passing conditions (weekday list etc.)
- `sun` trigger → fire time from `SunCalculator` (shared astral + tz path)
- `state` trigger → each matching transition in that day's events is a would-fire

Match: a `manual` event putting the action entity into the action state within the
window (tod/sun ±45 min = miner tolerance; pair = mined pair window).
`triggered_by='automation'` events never count. Records `expected_fires`,
`human_matches`, `human_total` per day.

Derived scores: **precision** = matches/expected (a wrong fire is the expensive
error); **coverage** = matches/human_total. UI shows 14-day rolling values and a
"ready" badge at ≥14 shadowed days with precision ≥ 0.8 and coverage ≥ 0.8
(settings). The badge is Phase 4 input; nothing acts on it.

Backfill: evaluator scores all un-scored (proposal, day) pairs up to 30 days back —
a missed night self-heals.

Scheduling: worker nightly chain mine → generate → shadow-eval; each stage enqueues
the next on completion. The miner job itself is unchanged.

## Web UI

Server-rendered on the existing `api` service: FastAPI + Jinja2, plain forms +
redirects (htmx only where a page genuinely needs it; no SPA, no JS chart libs).

- `/proposals` — tabs by status; rows: title, entity friendly names, kind, 14-day
  precision/coverage inline-SVG sparkbars, ready badge, age.
- `/proposals/{id}` — rationale, automation rendered as HA-style YAML, source pattern
  stats + sample times, daily shadow history, Approve / Reject (optional reason).
  POSTs mutate only the PAE db.

No auth this phase (LAN-only; mutations are reversible db flags). Phase 4 re-confirms
every approved proposal with the operator at HA-write time and adds a UI auth gate
before any write path exists.

## Observability

Prometheus (worker): LLM request count/duration/failures by model+host, proposals
gauge by status, validation-failure counter, shadow-eval days processed. Structlog
throughout, miner style.

## Testing

Offline by default (`-m 'not live'`): gates + grouping over synthetic pattern rows;
prompt-builder snapshot; schema validation with good/bad/hostile payloads (unknown
entity, wrong-domain service, template smuggling); LLM client failover against a fake
HTTP server; shadow evaluator over synthetic events including sun-trigger and DST
days; UI routes via FastAPI TestClient; migration up/down. One `-m live` Ollama
round-trip test.

## Acceptance (phase gate)

Several nights of unattended runs, then jointly: review proposals in the UI; verify
zero sched-flagged / dusk-to-dawn leakage into proposals; cross-check shadow numbers
against Grafana events; `git diff` shows `ALLOWED_OUTBOUND_TYPES` bit-identical.
Operator then decides the Phase 4 gate.

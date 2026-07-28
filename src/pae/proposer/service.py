"""Proposal-generation orchestration: eligible patterns -> groups -> LLM drafts
-> validated automations, persisted as shadowing proposals.

Runs synchronously (it is an RQ job); uses its own short-lived sync engine.
The decision core (`_process_groups`) is a pure function of its inputs so it
can be unit-tested with a fake LLM — `run_proposing` is the thin IO shell
that loads state, applies the decisions inside one transaction, and reports
metrics.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from pae.config import get_settings
from pae.db.models import EntityRegistryEntry, Pattern, Proposal
from pae.llm.client import LLMError, OllamaClient
from pae.llm.prompt import PROMPT_VERSION, RESPONSE_SCHEMA, RegistryInfo, build_messages
from pae.logging import get_logger
from pae.metrics import (
    LLM_REQUESTS,
    LLM_SECONDS,
    PROPOSALS_BY_STATUS,
    PROPOSER_VALIDATION_FAILURES,
)
from pae.miner.sun import SunCalculator
from pae.proposer.gates import eligible_patterns
from pae.proposer.grouping import ProposalGroup, build_groups
from pae.proposer.schema import validate_proposal

log = get_logger(__name__)

LIVE_STATUSES = ("shadowing", "approved")
# every status a Proposal row can carry — kept fixed so a bucket that drops to
# zero overwrites its stale last value instead of holding it forever (mirrors
# pae.miner.service's MINER_PATTERNS refresh).
PROPOSAL_STATUSES = ("shadowing", "approved", "rejected", "stale")


@dataclass
class ProposeResult:
    groups_considered: int
    generated: int
    declined: int
    validation_failed: int
    skipped_existing: int
    stale_marked: int


def _new_counters() -> dict[str, int]:
    return {
        "groups_considered": 0,
        "generated": 0,
        "declined": 0,
        "validation_failed": 0,
        "skipped_existing": 0,
    }


def _content_field_errors(content: dict) -> list[str]:
    """RESPONSE_SCHEMA only requires "propose" — title/rationale are schema-legal
    to omit, but an insert with either missing crashes the storage layer. Treat
    that as a validation failure so it goes through the same retry-once path
    as an invalid automation, rather than raising deep inside the transaction."""
    errors = []
    for field in ("title", "rationale"):
        value = content.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} is missing or empty")
    return errors


def _refresh_proposal_gauges(status_counts: dict[str, int]) -> None:
    for status_name in PROPOSAL_STATUSES:
        PROPOSALS_BY_STATUS.labels(status=status_name).set(status_counts.get(status_name, 0))


def _process_groups(
    groups: list[ProposalGroup],
    existing: dict[str, str],
    llm,
    registry: dict[str, RegistryInfo],
    registry_domains: dict[str, str],
    tz: ZoneInfo,
    sun: SunCalculator | None,
    now: datetime,
) -> tuple[dict[str, int], list[dict], dict[str, str]]:
    counters = _new_counters()
    inserts: list[dict] = []
    status_updates: dict[str, str] = {}

    for group in groups:
        counters["groups_considered"] += 1
        status = existing.get(group.group_key)

        if status == "rejected":
            counters["skipped_existing"] += 1
            continue

        if status in LIVE_STATUSES:
            counters["skipped_existing"] += 1
            continue

        if status == "stale":
            counters["skipped_existing"] += 1
            for p in group.patterns:
                status_updates[p.pattern_key] = "proposed"
            log.info("proposal_revived", group_key=group.group_key)
            continue

        messages = build_messages(group, registry, tz)
        try:
            resp = llm.chat_json(messages, RESPONSE_SCHEMA)
        except LLMError as exc:
            log.warning("llm_request_failed", group_key=group.group_key, error=str(exc))
            LLM_REQUESTS.labels(model="unknown", status="error").inc()
            continue

        content = resp.content
        if content.get("propose") is not True:
            counters["declined"] += 1
            log.info(
                "proposal_declined",
                group_key=group.group_key,
                decline_reason=content.get("decline_reason"),
            )
            continue

        parsed, errors = validate_proposal(
            content.get("automation", {}),
            group,
            registry_domains=registry_domains,
            tz=tz,
            sun=sun,
        )
        errors = errors + _content_field_errors(content)

        if errors:
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": f"Validation failed: {errors}. Return corrected JSON.",
                }
            ]
            try:
                resp = llm.chat_json(retry_messages, RESPONSE_SCHEMA)
            except LLMError as exc:
                log.warning(
                    "llm_retry_failed", group_key=group.group_key, error=str(exc)
                )
                counters["validation_failed"] += 1
                PROPOSER_VALIDATION_FAILURES.inc()
                LLM_REQUESTS.labels(model="unknown", status="error").inc()
                continue
            content = resp.content
            parsed, errors = validate_proposal(
                content.get("automation", {}),
                group,
                registry_domains=registry_domains,
                tz=tz,
                sun=sun,
            )
            errors = errors + _content_field_errors(content)
            if errors:
                counters["validation_failed"] += 1
                PROPOSER_VALIDATION_FAILURES.inc()
                log.warning(
                    "proposal_validation_failed", group_key=group.group_key, errors=errors
                )
                continue

        LLM_SECONDS.set(resp.duration_seconds)
        LLM_REQUESTS.labels(model=resp.model, status="ok").inc()
        inserts.append(
            {
                "group_key": group.group_key,
                "kind": group.kind,
                "title": content["title"],
                "rationale": content["rationale"],
                "automation_json": parsed.model_dump(by_alias=True, exclude_none=True),
                "source_pattern_keys": [p.pattern_key for p in group.patterns],
                "entity_ids": group.entity_ids,
                "model_name": resp.model,
                "prompt_version": PROMPT_VERSION,
                "status": "shadowing",
                "last_eligible_at": now,
            }
        )
        counters["generated"] += 1
        for p in group.patterns:
            status_updates[p.pattern_key] = "proposed"

    return counters, inserts, status_updates


def run_proposing(now: datetime | None = None, llm=None) -> ProposeResult:
    settings = get_settings()
    now = now or datetime.now(UTC)
    tz = ZoneInfo(settings.miner_local_tz)
    sun = None
    if settings.miner_latitude is not None and settings.miner_longitude is not None:
        sun = SunCalculator(settings.miner_latitude, settings.miner_longitude, tz)
    if llm is None:
        llm = OllamaClient(
            primary_url=settings.ollama_primary,
            fallback_url=settings.ollama_fallback,
            primary_model=settings.llm_model_primary,
            fallback_model=settings.llm_model_fallback,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    engine = sa.create_engine(settings.db_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            patterns = list(conn.execute(sa.select(Pattern)))
            registry_rows = list(conn.execute(sa.select(EntityRegistryEntry)))
            registry = {
                r.entity_id: RegistryInfo(r.domain, r.friendly_name, r.area_name, r.device_class)
                for r in registry_rows
            }
            registry_domains = {r.entity_id: r.domain for r in registry_rows}
            registry_ids = set(registry_domains)

            eligible = eligible_patterns(
                patterns,
                registry_ids=registry_ids,
                tod_min_consistency=settings.proposer_tod_min_consistency,
                tod_max_std_minutes=settings.proposer_tod_max_std_minutes,
                tod_min_support=settings.proposer_tod_min_support,
                tod_min_days=settings.proposer_tod_min_days,
                min_occurrences=settings.proposer_min_occurrences,
                pair_min_confidence=settings.proposer_pair_min_confidence,
                pair_min_lift=settings.proposer_pair_min_lift,
            )
            groups = build_groups(eligible, window_minutes=settings.proposer_group_window_minutes)

            existing = dict(
                conn.execute(sa.select(Proposal.group_key, Proposal.status)).all()
            )

            counters, inserts, status_updates = _process_groups(
                groups, existing, llm, registry, registry_domains, tz, sun, now
            )

            live_keys = [g.group_key for g in groups if existing.get(g.group_key) in LIVE_STATUSES]
            if live_keys:
                conn.execute(
                    sa.update(Proposal)
                    .where(Proposal.group_key.in_(live_keys))
                    .values(last_eligible_at=now, updated_at=now)
                )

            stale_keys = [g.group_key for g in groups if existing.get(g.group_key) == "stale"]
            if stale_keys:
                conn.execute(
                    sa.update(Proposal)
                    .where(Proposal.group_key.in_(stale_keys))
                    .values(status="shadowing", last_eligible_at=now, updated_at=now)
                )

            if inserts:
                rows = [dict(row, created_at=now, updated_at=now) for row in inserts]
                conn.execute(sa.insert(Proposal), rows)

            if status_updates:
                conn.execute(
                    sa.update(Pattern)
                    .where(
                        Pattern.pattern_key.in_(list(status_updates)),
                        Pattern.status == "candidate",
                    )
                    .values(status="proposed")
                )

            # staleness sweep: proposals that fell out of eligibility long enough ago
            cutoff = now - timedelta(days=settings.proposer_stale_days)
            gone_stale = list(
                conn.execute(
                    sa.select(Proposal.id, Proposal.source_pattern_keys).where(
                        Proposal.status.in_(LIVE_STATUSES),
                        Proposal.last_eligible_at < cutoff,
                    )
                )
            )
            stale_marked = len(gone_stale)
            if gone_stale:
                stale_ids = [r.id for r in gone_stale]
                conn.execute(
                    sa.update(Proposal)
                    .where(Proposal.id.in_(stale_ids))
                    .values(status="stale", updated_at=now)
                )
                revert_keys: set[str] = set()
                for r in gone_stale:
                    revert_keys.update(r.source_pattern_keys)
                if revert_keys:
                    conn.execute(
                        sa.update(Pattern)
                        .where(
                            Pattern.pattern_key.in_(revert_keys),
                            Pattern.status == "proposed",
                        )
                        .values(status="candidate")
                    )

            status_counts = dict(
                conn.execute(
                    sa.select(Proposal.status, sa.func.count()).group_by(Proposal.status)
                ).all()
            )
        _refresh_proposal_gauges(status_counts)

        result = ProposeResult(
            groups_considered=counters["groups_considered"],
            generated=counters["generated"],
            declined=counters["declined"],
            validation_failed=counters["validation_failed"],
            skipped_existing=counters["skipped_existing"],
            stale_marked=stale_marked,
        )
        log.info(
            "propose_done",
            groups_considered=result.groups_considered,
            generated=result.generated,
            declined=result.declined,
            validation_failed=result.validation_failed,
            skipped_existing=result.skipped_existing,
            stale_marked=result.stale_marked,
        )
        return result
    finally:
        engine.dispose()

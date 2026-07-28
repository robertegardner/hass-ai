"""Deterministic eligibility gates: what the LLM is allowed to see.

Precision-biased: any doubt excludes the pattern. The sched-sibling rule
covers both kinds — an entity with ANY suspected_schedule time_of_day
pattern is off-limits everywhere (CLAUDE.md miner invariant)."""
from collections.abc import Sequence


def sched_flagged_entities(patterns: Sequence) -> set[str]:
    return {
        p.entity_id
        for p in patterns
        if p.kind == "time_of_day" and p.suspected_schedule
    }


def eligible_patterns(
    patterns: Sequence,
    *,
    registry_ids: set[str],
    tod_min_consistency: float = 0.8,
    tod_max_std_minutes: float = 30.0,
    tod_min_support: float = 0.6,
    tod_min_days: int = 14,
    min_occurrences: int = 8,
    pair_min_confidence: float = 0.7,
    pair_min_lift: float = 5.0,
) -> list:
    flagged = sched_flagged_entities(patterns)
    out = []
    for p in patterns:
        if p.suspected_schedule or p.status == "rejected":
            continue
        if p.entity_id in flagged or p.entity_id not in registry_ids:
            continue
        if p.occurrences < min_occurrences:
            continue
        if p.kind == "time_of_day":
            if p.temporal_consistency is None or p.temporal_consistency < tod_min_consistency:
                continue
            if p.tod_std_minutes is None or p.tod_std_minutes > tod_max_std_minutes:
                continue
            if p.support < tod_min_support or p.days_observed < tod_min_days:
                continue
        elif p.kind == "event_pair":
            if p.trigger_entity_id in flagged or p.trigger_entity_id not in registry_ids:
                continue
            if p.confidence < pair_min_confidence or p.lift < pair_min_lift:
                continue
        else:
            continue
        out.append(p)
    return out

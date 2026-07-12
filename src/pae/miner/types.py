"""Data types shared by the pattern miners.

``MinedEvent`` field order matches the SELECT in ``pae.miner.service.load_events``.
``PatternCandidate`` field names match ``pae.db.models.Pattern`` column names so
``as_row`` can feed a bulk upsert directly.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

ACTIONABLE_DOMAINS = frozenset(
    {"light", "switch", "climate", "cover", "lock", "fan", "media_player"}
)


@dataclass(frozen=True)
class MinedEvent:
    time: datetime  # tz-aware UTC
    entity_id: str
    domain: str
    old_state: str | None
    new_state: str | None
    triggered_by: str
    user_id: str | None


@dataclass(frozen=True)
class PatternCandidate:
    kind: str  # time_of_day | event_pair
    pattern_key: str
    entity_id: str
    action: str
    day_type: str | None
    trigger_entity_id: str | None
    trigger_state: str | None
    tod_minutes: float | None
    tod_std_minutes: float | None
    support: float
    confidence: float
    lift: float
    temporal_consistency: float | None
    occurrences: int
    days_observed: int
    suspected_schedule: bool
    evidence: dict[str, Any]
    first_seen: datetime
    last_seen: datetime

    def as_row(self, mined_at: datetime) -> dict[str, Any]:
        return {**asdict(self), "mined_at": mined_at}

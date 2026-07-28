from datetime import UTC, datetime

from pae.db.models import Pattern

NOW = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)


def make_pattern(**kw) -> Pattern:
    defaults = dict(
        pattern_key="tod:light.bar:on:weekday:14",
        kind="time_of_day",
        entity_id="light.bar",
        action="on",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=432.0,
        tod_std_minutes=8.0,
        support=0.9,
        confidence=0.9,
        lift=20.0,
        temporal_consistency=0.95,
        occurrences=15,
        days_observed=16,
        suspected_schedule=False,
        status="candidate",
        evidence={},
        first_seen=NOW,
        last_seen=NOW,
        mined_at=NOW,
    )
    defaults.update(kw)
    return Pattern(**defaults)

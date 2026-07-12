from datetime import UTC, datetime

from pae.miner.types import PatternCandidate


def _candidate() -> PatternCandidate:
    t = datetime(2026, 7, 10, 3, 12, tzinfo=UTC)
    return PatternCandidate(
        kind="time_of_day",
        pattern_key="tod:light.bar:off:weekday:44",
        entity_id="light.bar",
        action="off",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=1332.0,
        tod_std_minutes=4.0,
        support=1.0,
        confidence=1.0,
        lift=16.0,
        temporal_consistency=1.0,
        occurrences=5,
        days_observed=5,
        suspected_schedule=False,
        evidence={"sample_times": [t.isoformat()]},
        first_seen=t,
        last_seen=t,
    )


def test_as_row_matches_column_names():
    mined_at = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)
    row = _candidate().as_row(mined_at)
    assert row["pattern_key"] == "tod:light.bar:off:weekday:44"
    assert row["mined_at"] == mined_at
    assert "status" not in row  # status lifecycle is owned by later phases
    assert "id" not in row

from dataclasses import replace
from datetime import UTC, date, datetime

import holidays

from pae.miner.service import day_type_of, dedupe_candidates, reconcile_keys
from pae.miner.types import PatternCandidate

US = holidays.US()


def test_weekday():
    assert day_type_of(date(2026, 7, 8), US) == "weekday"  # Wednesday


def test_weekend():
    assert day_type_of(date(2026, 7, 11), US) == "weekend"  # Saturday


def test_holiday_counts_as_weekend():
    assert day_type_of(date(2026, 7, 3), US) == "weekend"  # July 4th observed


def tod_candidate(key: str, tod_minutes: float, occurrences: int = 5) -> PatternCandidate:
    t = datetime(2026, 7, 10, 3, 12, tzinfo=UTC)
    return PatternCandidate(
        kind="time_of_day",
        pattern_key=key,
        entity_id="light.bar",
        action="off",
        day_type="weekday",
        trigger_entity_id=None,
        trigger_state=None,
        tod_minutes=tod_minutes,
        tod_std_minutes=4.0,
        support=1.0,
        confidence=1.0,
        lift=16.0,
        temporal_consistency=1.0,
        occurrences=occurrences,
        days_observed=5,
        suspected_schedule=False,
        evidence={},
        first_seen=t,
        last_seen=t,
    )


def test_reconcile_adopts_existing_key_across_bucket_boundary():
    # mean drifted 20:59 -> 21:01: new bucket (42), but same habit as stored key
    cand = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 1)
    existing = [("tod:light.bar:off:weekday:41", "light.bar", "off", "weekday", 20 * 60 + 59.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "tod:light.bar:off:weekday:41"
    assert out.tod_minutes == cand.tod_minutes  # only the key changes


def test_reconcile_keeps_key_when_no_match_within_tolerance():
    cand = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 1)
    existing = [("tod:light.bar:off:weekday:14", "light.bar", "off", "weekday", 7 * 60 + 30.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "tod:light.bar:off:weekday:42"


def test_reconcile_nearest_candidate_claims_key_once():
    near = tod_candidate("tod:light.bar:off:weekday:42", 21 * 60 + 5)
    far = tod_candidate("tod:light.bar:off:weekday:43", 21 * 60 + 40)
    existing = [("tod:light.bar:off:weekday:42", "light.bar", "off", "weekday", 21 * 60 + 0.0)]
    out = {c.tod_minutes: c.pattern_key for c in reconcile_keys([far, near], existing)}
    assert out[21 * 60 + 5] == "tod:light.bar:off:weekday:42"
    assert out[21 * 60 + 40] == "tod:light.bar:off:weekday:43"


def test_reconcile_ignores_event_pair_candidates():
    cand = replace(
        tod_candidate("pair:a:on->b:on", 0.0), kind="event_pair", tod_minutes=None
    )
    existing = [("tod:light.bar:off:weekday:41", "light.bar", "off", "weekday", 0.0)]
    (out,) = reconcile_keys([cand], existing)
    assert out.pattern_key == "pair:a:on->b:on"


def test_dedupe_keeps_candidate_with_more_occurrences():
    a = tod_candidate("tod:light.bar:off:weekday:42", 100.0, occurrences=4)
    b = tod_candidate("tod:light.bar:off:weekday:42", 105.0, occurrences=7)
    (out,) = dedupe_candidates([a, b])
    assert out.occurrences == 7

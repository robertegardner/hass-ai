from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pae.miner.tod import mine_time_of_day
from pae.miner.types import MinedEvent

TZ = ZoneInfo("America/Chicago")
BASE = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)  # Monday


def day_type_for(dt: datetime) -> str:
    return "weekday" if dt.astimezone(TZ).weekday() < 5 else "weekend"


def ev(
    day: int,
    hh: int,
    mm: int,
    *,
    entity: str = "light.bar",
    state: str = "on",
    old: str = "off",
    triggered_by: str = "manual",
    user_id: str | None = None,
) -> MinedEvent:
    t = (BASE + timedelta(days=day)).replace(hour=hh, minute=mm)
    return MinedEvent(
        time=t.astimezone(UTC),
        entity_id=entity,
        domain=entity.split(".")[0],
        old_state=old,
        new_state=state,
        triggered_by=triggered_by,
        user_id=user_id,
    )


def mine(events, **kw):
    defaults = dict(
        tz=TZ,
        day_type_for=day_type_for,
        days_observed={"weekday": 5, "weekend": 2},
        min_support=0.5,
        min_occurrences=4,
        tolerance_minutes=45.0,
        schedule_std_minutes=2.0,
    )
    defaults.update(kw)
    return mine_time_of_day(events, **defaults)


def test_nightly_habit_is_mined():
    # Mon..Fri around 22:16, ±6 min of human jitter (std > schedule_std_minutes)
    events = [ev(d, 22, 10 + 3 * d) for d in range(5)]
    (p,) = mine(events)
    assert p.kind == "time_of_day"
    assert p.entity_id == "light.bar"
    assert p.action == "on"
    assert p.day_type == "weekday"
    assert p.support == pytest.approx(1.0)
    assert p.confidence == p.support
    assert p.temporal_consistency == pytest.approx(1.0)
    assert p.lift > 10
    assert p.occurrences == 5
    assert p.days_observed == 5
    assert not p.suspected_schedule
    assert p.pattern_key.startswith("tod:light.bar:on:weekday:")
    assert len(p.evidence["sample_times"]) == 5


def test_too_few_occurrences_filtered():
    assert mine([ev(d, 22, 10) for d in range(3)]) == []


def test_low_support_filtered():
    # 4 occurrences all on one weekday: support 1/5
    events = [ev(0, h, 0) for h in (21, 21, 21, 21)]
    assert mine(events) == []


def test_automation_and_nonactionable_ignored():
    auto = [ev(d, 22, 10, triggered_by="automation") for d in range(5)]
    sensor = [ev(d, 22, 10, entity="binary_sensor.motion") for d in range(5)]
    assert mine(auto + sensor) == []


def test_clockwork_device_flagged_as_schedule():
    events = [ev(d, 13, 2, entity="switch.pool_2") for d in range(5)]
    (p,) = mine(events)
    assert p.suspected_schedule
    assert p.tod_std_minutes == pytest.approx(0.0)


def test_clockwork_with_user_action_not_flagged():
    events = [ev(d, 13, 2, entity="switch.pool_2") for d in range(4)]
    events.append(ev(4, 13, 2, entity="switch.pool_2", user_id="83f9e619"))
    (p,) = mine(events)
    assert not p.suspected_schedule


def test_morning_and_evening_clusters_are_separate_patterns():
    events = [ev(d, 7, 30 + d) for d in range(5)] + [ev(d, 22, 10 + d) for d in range(5)]
    patterns = sorted(mine(events), key=lambda p: p.tod_minutes)
    assert len(patterns) == 2
    assert patterns[0].tod_minutes == pytest.approx(7 * 60 + 32, abs=1)
    assert patterns[1].tod_minutes == pytest.approx(22 * 60 + 12, abs=1)


def test_no_transition_ignored():
    events = [ev(d, 22, 10, old="on", state="on") for d in range(5)]
    assert mine(events) == []

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from astral import Observer
from astral.sun import sun as astral_sun

from pae.miner.sun import SunCalculator
from pae.miner.tod import mine_time_of_day
from pae.miner.types import MinedEvent

TZ = ZoneInfo("America/Chicago")
BASE = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)  # Monday
SUN = SunCalculator(41.85, -87.65, TZ)
_OBSERVER = Observer(latitude=41.85, longitude=-87.65)

# Mon..Fri over three weeks: long enough a span for the sun to drift measurably
WEEKDAYS_3W = [0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18]
OBS_3W = {"weekday": 15, "weekend": 6}


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


def sun_ev(
    day: int | date,
    *,
    anchor: str = "sunset",
    offset: float = 0.0,
    entity: str = "switch.dusk_to_dawn",
    state: str = "on",
    user_id: str | None = None,
) -> MinedEvent:
    d = (BASE + timedelta(days=day)).date() if isinstance(day, int) else day
    t = astral_sun(_OBSERVER, date=d, tzinfo=TZ)[anchor] + timedelta(minutes=offset)
    return MinedEvent(
        time=t.astimezone(UTC),
        entity_id=entity,
        domain=entity.split(".")[0],
        old_state="on" if state == "off" else "off",
        new_state=state,
        triggered_by="manual",
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
        sun=SUN,
        sun_std_minutes=5.0,
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


def test_sunset_tracker_flagged():
    events = [sun_ev(d, offset=2 * (d % 2)) for d in WEEKDAYS_3W]
    (p,) = mine(events, days_observed=OBS_3W)
    assert p.suspected_schedule
    assert p.evidence["schedule_anchor"] == "sunset"
    assert p.evidence["sun_offset_std_minutes"] <= 2
    assert p.tod_std_minutes > 2.0  # the clock rule alone would not have fired


def test_sunrise_tracker_flagged():
    events = [sun_ev(d, anchor="sunrise", state="off", offset=2 * (d % 2)) for d in WEEKDAYS_3W]
    (p,) = mine(events, days_observed=OBS_3W)
    assert p.suspected_schedule
    assert p.evidence["schedule_anchor"] == "sunrise"


def test_sun_tracker_with_user_action_not_flagged():
    events = [sun_ev(d, offset=2 * (d % 2)) for d in WEEKDAYS_3W[:-1]]
    events.append(sun_ev(WEEKDAYS_3W[-1], user_id="83f9e619"))
    (p,) = mine(events, days_observed=OBS_3W)
    assert not p.suspected_schedule


def test_sun_detection_disabled_without_calculator():
    events = [sun_ev(d, offset=2 * (d % 2)) for d in WEEKDAYS_3W]
    (p,) = mine(events, days_observed=OBS_3W, sun=None)
    assert not p.suspected_schedule


def test_short_span_sun_tracker_not_flagged():
    # ±4 min jitter defeats the clock rule and fits the sun anchor (std ~3.9),
    # but a 5-day window is below sun_min_span_days — the sun rule must not
    # apply where sun drift is indistinguishable from clock drift
    events = [sun_ev(d, offset=4 if d % 2 == 0 else -4) for d in range(5)]
    (p,) = mine(events)
    assert not p.suspected_schedule


def test_long_window_clock_habit_not_sun_flagged():
    # fixed 21:30 with ±4 min jitter over 3 weeks: clock std ~2.8 (> 2.0), and
    # sun offsets inherit that jitter plus seasonal drift, fitting worse than
    # the clock — the better-than-clock guard must reject it
    jitter = [-4, -2, 0, 2, 4]
    events = [ev(d, 21, 30 + jitter[i % 5]) for i, d in enumerate(WEEKDAYS_3W)]
    (p,) = mine(events, days_observed=OBS_3W)
    assert p.tod_std_minutes > 2.0
    assert not p.suspected_schedule


def test_sun_tracker_across_dst_transition():
    # weekdays either side of the 2026-11-01 fall-back: local sunset drops
    # ~60 min mid-window, so the clock view sees huge spread while sun
    # offsets stay constant — only the sun anchor can catch this tracker
    days = [date(2026, 10, 26) + timedelta(days=i) for i in range(5)]
    days += [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]
    events = [sun_ev(d, offset=15.0) for d in days]
    (p,) = mine(events, days_observed={"weekday": 10, "weekend": 4})
    assert p.suspected_schedule
    assert p.evidence["schedule_anchor"] == "sunset"
    assert p.tod_std_minutes > 20


def test_clock_schedule_evidence_anchor():
    events = [ev(d, 13, 2, entity="switch.pool_2") for d in range(5)]
    (p,) = mine(events)
    assert p.suspected_schedule
    assert p.evidence["schedule_anchor"] == "clock"
    assert "sun_offset_std_minutes" not in p.evidence

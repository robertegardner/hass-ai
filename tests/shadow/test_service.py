from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pae.miner.sun import SunCalculator
from pae.shadow.service import ShadowEvent, evaluate_day

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
DAY = date(2026, 7, 27)  # a Monday


def ev(hh, mm, entity="switch.a", state="on", triggered_by="manual", day=DAY):
    t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
    return ShadowEvent(time=t.astimezone(UTC), entity_id=entity, new_state=state,
                       triggered_by=triggered_by)


TOD = {
    "trigger": [{"platform": "time", "at": "07:12:00"}],
    "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
    "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
}


def test_tod_hit():
    score = evaluate_day(TOD, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(7, 20)])
    assert (score.expected_fires, score.human_matches, score.human_total) == (1, 1, 1)


def test_tod_miss_and_out_of_window_human():
    score = evaluate_day(TOD, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(12, 0)])
    assert (score.expected_fires, score.human_matches, score.human_total) == (1, 0, 1)


def test_weekday_condition_blocks_weekend():
    sunday = date(2026, 7, 26)
    score = evaluate_day(
        TOD, "time_of_day", sunday, tz=TZ, sun=SUN, day_events=[ev(7, 12, day=sunday)]
    )
    assert score.expected_fires == 0 and score.human_total == 1


def test_automation_events_never_match():
    score = evaluate_day(
        TOD, "time_of_day", DAY, tz=TZ, sun=SUN,
        day_events=[ev(7, 12, triggered_by="automation")],
    )
    assert (score.human_matches, score.human_total) == (0, 0)


def test_sun_trigger_fires_at_sunset():
    auto = dict(TOD, trigger=[{"platform": "sun", "event": "sunset", "offset": "00:10:00"}],
                condition=[])
    sunset = SUN.sun_minutes(DAY)[1]
    hh, mm = int((sunset + 10) // 60), int((sunset + 10) % 60)
    score = evaluate_day(auto, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[ev(hh, mm)])
    assert (score.expected_fires, score.human_matches) == (1, 1)


def test_pair_counts_each_trigger_occurrence():
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    events = [
        ev(9, 0, entity="binary_sensor.door"),
        ev(9, 2),                                  # within 5-min pair window -> match
        ev(15, 0, entity="binary_sensor.door"),    # no follow-up -> miss
    ]
    score = evaluate_day(auto, "event_pair", DAY, tz=TZ, sun=SUN, day_events=events)
    assert (score.expected_fires, score.human_matches, score.human_total) == (2, 1, 1)


def test_pair_window_is_forward_only():
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    events = [ev(9, 0), ev(9, 2, entity="binary_sensor.door")]  # human BEFORE trigger
    score = evaluate_day(auto, "event_pair", DAY, tz=TZ, sun=SUN, day_events=events)
    assert score.human_matches == 0


def test_dst_fallback_sun_eval():
    # sun trigger evaluates on the DST fall-back day without error
    d = date(2026, 11, 1)
    auto = dict(TOD, trigger=[{"platform": "sun", "event": "sunset", "offset": "00:00:00"}],
                condition=[])
    sunset = SUN.sun_minutes(d)[1]
    hh, mm = int(sunset // 60), int(sunset % 60)
    score = evaluate_day(auto, "time_of_day", d, tz=TZ, sun=SUN,
                         day_events=[ev(hh, mm, day=d)])
    assert score.human_matches == 1


def test_adjacent_event_matches_across_midnight():
    # a time trigger just after midnight must see a human event from the
    # tail end of the previous calendar day, via adjacent_events only
    auto = dict(TOD, trigger=[{"platform": "time", "at": "00:10:00"}], condition=[])
    prev_day = DAY - timedelta(days=1)
    score = evaluate_day(
        auto, "time_of_day", DAY, tz=TZ, sun=SUN, day_events=[],
        adjacent_events=[ev(23, 55, day=prev_day)],
    )
    assert (score.human_matches, score.human_total) == (1, 0)


def test_adjacent_events_do_not_count_as_state_trigger_fires():
    # a door-open event that only appears in adjacent_events must never be
    # counted as a would-fire occurrence for a state trigger
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    prev_day = DAY - timedelta(days=1)
    score = evaluate_day(
        auto, "event_pair", DAY, tz=TZ, sun=SUN, day_events=[],
        adjacent_events=[ev(23, 55, entity="binary_sensor.door", day=prev_day)],
    )
    assert score.expected_fires == 0


def test_pair_forward_window_matches_across_midnight():
    # trigger fires at 23:58 (day_events); the human's follow-up at 00:02 the
    # next day is 4 real minutes later and must count as a forward match
    auto = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    next_day = DAY + timedelta(days=1)
    score = evaluate_day(
        auto, "event_pair", DAY, tz=TZ, sun=SUN,
        day_events=[ev(23, 58, entity="binary_sensor.door")],
        adjacent_events=[ev(0, 2, day=next_day)],
    )
    assert (score.expected_fires, score.human_matches) == (1, 1)

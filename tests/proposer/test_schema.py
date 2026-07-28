from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from conftest import make_pattern

from pae.miner.sun import SunCalculator
from pae.proposer.grouping import build_groups
from pae.proposer.schema import validate_proposal

TZ = ZoneInfo("America/Chicago")
SUN = SunCalculator(41.85, -87.65, TZ)
REG = {"switch.a": "switch", "light.bar": "light", "binary_sensor.door": "binary_sensor"}


def tod_group(minutes=432.0):
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="tod:switch.a:on:weekday:14",
                entity_id="switch.a",
                tod_minutes=minutes,
            )
        ]
    )
    return g


def pair_group():
    (g,) = build_groups(
        [
            make_pattern(
                pattern_key="pair:binary_sensor.door:on->light.bar:on",
                kind="event_pair",
                entity_id="light.bar",
                day_type=None,
                tod_minutes=None,
                trigger_entity_id="binary_sensor.door",
                trigger_state="on",
            )
        ]
    )
    return g


def check(automation, group, **kw):
    return validate_proposal(
        automation, group, registry_domains=REG, tz=TZ, sun=SUN, **kw
    )


GOOD_TOD = {
    "trigger": [{"platform": "time", "at": "07:12:00"}],
    "condition": [{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}],
    "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
}


def test_valid_tod_automation_passes():
    parsed, errors = check(GOOD_TOD, tod_group())
    assert errors == []
    assert parsed.trigger[0].at == "07:12:00"


def test_unknown_top_level_key_rejected():
    bad = dict(GOOD_TOD, mode="restart")
    parsed, errors = check(bad, tod_group())
    assert parsed is None and errors


def test_foreign_entity_rejected():
    bad = {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["light.bar"]}}],
    }
    parsed, errors = check(bad, tod_group())
    assert parsed is None
    assert any("light.bar" in e for e in errors)


def test_service_domain_mismatch_rejected():
    bad = {
        "trigger": [{"platform": "time", "at": "07:12:00"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    parsed, errors = check(bad, tod_group())
    assert parsed is None


def test_trigger_time_far_from_mined_mean_rejected():
    bad = dict(GOOD_TOD, trigger=[{"platform": "time", "at": "12:00:00"}])
    parsed, errors = check(bad, tod_group())
    assert parsed is None
    assert any("mined" in e for e in errors)


def test_template_smuggling_rejected():
    bad = dict(
        GOOD_TOD,
        action=[
            {
                "service": "switch.turn_on",
                "target": {"entity_id": ["switch.a"]},
                "data": {"x": "{{ states('sensor.hack') }}"},
            }
        ],
    )
    parsed, errors = check(bad, tod_group())
    assert parsed is None


def test_sun_trigger_near_mined_mean_passes():
    # group mean at today's sunset; sun trigger with zero offset must pass
    sunset = SUN.sun_minutes(datetime.now(UTC).astimezone(TZ).date())[1]
    auto = {
        "trigger": [{"platform": "sun", "event": "sunset", "offset": "00:00:00"}],
        "condition": [],
        "action": [{"service": "switch.turn_on", "target": {"entity_id": ["switch.a"]}}],
    }
    parsed, errors = check(auto, tod_group(minutes=sunset))
    assert errors == []


def test_pair_trigger_must_match_mined_trigger():
    good = {
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "condition": [],
        "action": [{"service": "light.turn_on", "target": {"entity_id": ["light.bar"]}}],
    }
    parsed, errors = check(good, pair_group())
    assert errors == []
    bad = dict(
        good, trigger=[{"platform": "state", "entity_id": "binary_sensor.door", "to": "off"}]
    )
    parsed, errors = check(bad, pair_group())
    assert parsed is None

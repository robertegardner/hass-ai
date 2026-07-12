from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pae.miner.pairs import mine_event_pairs
from pae.miner.types import MinedEvent

TZ = ZoneInfo("America/Chicago")
BASE = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)  # Monday


def ev(
    day: int,
    hh: int,
    mm: int,
    ss: int = 0,
    *,
    entity: str,
    state: str = "on",
    old: str = "off",
    triggered_by: str = "manual",
) -> MinedEvent:
    t = (BASE + timedelta(days=day)).replace(hour=hh, minute=mm, second=ss)
    return MinedEvent(
        time=t.astimezone(UTC),
        entity_id=entity,
        domain=entity.split(".")[0],
        old_state=old,
        new_state=state,
        triggered_by=triggered_by,
        user_id=None,
    )


def mine(events, **kw):
    defaults = dict(
        tz=TZ,
        days_observed_total=6,
        span_minutes=6 * 1440.0,
        window_minutes=5.0,
        min_gap_seconds=2.0,
        min_pairs=4,
        min_confidence=0.6,
        min_lift=3.0,
    )
    defaults.update(kw)
    return mine_event_pairs(events, **defaults)


def tv_then_light(days: int) -> list[MinedEvent]:
    out = []
    for d in range(days):
        out.append(ev(d, 19, 0, entity="media_player.tv", state="playing", old="idle"))
        out.append(ev(d, 19, 0, 30, entity="light.den", state="on"))
    return out


def test_trigger_then_action_is_mined():
    events = tv_then_light(5)
    # one TV-on without the light, so confidence is 5/6
    events.append(ev(5, 19, 0, entity="media_player.tv", state="playing", old="idle"))
    (p,) = mine(events)
    assert p.kind == "event_pair"
    assert p.trigger_entity_id == "media_player.tv"
    assert p.trigger_state == "playing"
    assert p.entity_id == "light.den"
    assert p.action == "on"
    assert p.confidence == pytest.approx(5 / 6, abs=0.01)
    assert p.lift > 3.0
    assert p.occurrences == 5
    assert p.support == pytest.approx(5 / 6, abs=0.01)
    assert p.pattern_key == "pair:media_player.tv:playing->light.den:on"


def test_too_few_pairs_filtered():
    assert mine(tv_then_light(3)) == []


def test_low_confidence_filtered():
    events = tv_then_light(4)
    for d in range(4):  # 12 extra TV-ons with no light: confidence 4/16
        for hh in (9, 12, 15):
            events.append(ev(d, hh, 0, entity="media_player.tv", state="playing", old="idle"))
    assert mine(events) == []


def test_same_entity_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, entity="light.den", state="on"))
        events.append(ev(d, 19, 1, entity="light.den", state="off", old="on"))
    assert mine(events) == []


def test_near_simultaneous_mirror_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, 0, entity="switch.fan", state="on"))
        events.append(ev(d, 19, 0, 1, entity="fan.fan", state="on"))
    assert mine(events) == []


def test_automation_consequent_excluded():
    events = []
    for d in range(5):
        events.append(ev(d, 19, 0, entity="media_player.tv", state="playing", old="idle"))
        events.append(ev(d, 19, 0, 30, entity="light.den", state="on", triggered_by="automation"))
    assert mine(events) == []


def test_low_lift_filtered():
    # B is so common that following A carries no information:
    # confidence and pair count pass, the lift gate must reject it
    events = tv_then_light(5)
    for d in range(5):
        for hh in (6, 9, 12, 15):
            events.append(ev(d, hh, 0, entity="light.den", state="on"))
            events.append(ev(d, hh, 30, entity="light.den", state="off", old="on"))
    assert mine(events, span_minutes=300.0) == []

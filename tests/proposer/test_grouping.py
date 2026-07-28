from conftest import make_pattern

from pae.proposer.grouping import build_groups


def tod(entity, minutes, day_type="weekday", action="on"):
    return make_pattern(
        pattern_key=f"tod:{entity}:{action}:{day_type}:{int(minutes // 30):02d}",
        entity_id=entity,
        action=action,
        day_type=day_type,
        tod_minutes=minutes,
    )


def test_close_tod_patterns_group_together():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.b", 440.0)])
    (g,) = groups
    assert g.kind == "time_of_day"
    assert g.entity_actions == [("switch.a", "on"), ("switch.b", "on")]
    assert 432.0 <= g.mean_minutes <= 440.0


def test_distant_tod_patterns_split():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.b", 500.0)])
    assert len(groups) == 2


def test_day_types_never_mix():
    groups = build_groups([tod("switch.a", 432.0), tod("switch.a", 432.0, day_type="weekend")])
    assert len(groups) == 2


def test_pairs_group_by_trigger():
    def pair(entity):
        return make_pattern(
            pattern_key=f"pair:binary_sensor.door:on->{entity}:on",
            kind="event_pair",
            entity_id=entity,
            day_type=None,
            tod_minutes=None,
            trigger_entity_id="binary_sensor.door",
            trigger_state="on",
        )

    (g,) = build_groups([pair("light.bar"), pair("switch.a")])
    assert g.kind == "event_pair"
    assert g.trigger_entity_id == "binary_sensor.door"
    assert g.entity_ids == ["light.bar", "switch.a"]


def test_group_key_stable_regardless_of_input_order():
    a, b = tod("switch.a", 432.0), tod("switch.b", 440.0)
    (g1,) = build_groups([a, b])
    (g2,) = build_groups([b, a])
    assert g1.group_key == g2.group_key
    assert g1.group_key.startswith("tod:weekday:")

from conftest import make_pattern  # noqa: F401 — repo rule: never tests.conftest

from pae.proposer.gates import eligible_patterns, sched_flagged_entities

REG = {"light.bar", "switch.a", "switch.b", "binary_sensor.door"}


def elig(pats, **kw):
    return eligible_patterns(pats, registry_ids=REG, **kw)


def test_good_tod_pattern_is_eligible():
    assert elig([make_pattern()]) != []


def test_sched_flagged_excluded():
    assert elig([make_pattern(suspected_schedule=True)]) == []


def test_sched_sibling_excluded_across_kinds():
    flagged = make_pattern(
        pattern_key="tod:switch.a:on:weekday:40", entity_id="switch.a", suspected_schedule=True
    )
    pair = make_pattern(
        pattern_key="pair:binary_sensor.door:on->switch.a:on",
        kind="event_pair",
        entity_id="switch.a",
        trigger_entity_id="binary_sensor.door",
        trigger_state="on",
        confidence=0.9,
        lift=10.0,
    )
    assert elig([flagged, pair]) == []
    assert sched_flagged_entities([flagged, pair]) == {"switch.a"}


def test_pair_with_flagged_trigger_entity_excluded():
    flagged = make_pattern(
        pattern_key="tod:binary_sensor.door:on:weekday:40",
        entity_id="binary_sensor.door",
        suspected_schedule=True,
    )
    pair = make_pattern(
        pattern_key="pair:binary_sensor.door:on->light.bar:on",
        kind="event_pair",
        trigger_entity_id="binary_sensor.door",
        trigger_state="on",
        confidence=0.9,
        lift=10.0,
    )
    assert elig([flagged, pair]) == []


def test_threshold_gates():
    assert elig([make_pattern(temporal_consistency=0.7)]) == []
    assert elig([make_pattern(tod_std_minutes=31.0)]) == []
    assert elig([make_pattern(support=0.5)]) == []
    assert elig([make_pattern(occurrences=7)]) == []
    assert elig([make_pattern(days_observed=13)]) == []
    pair_kw = dict(
        kind="event_pair", trigger_entity_id="binary_sensor.door", trigger_state="on"
    )
    assert elig([make_pattern(**pair_kw, confidence=0.6, lift=10.0)]) == []
    assert elig([make_pattern(**pair_kw, confidence=0.9, lift=4.0)]) == []
    assert elig([make_pattern(**pair_kw, confidence=0.9, lift=10.0)]) != []


def test_unknown_entity_and_rejected_status_excluded():
    assert elig([make_pattern(entity_id="light.gone")]) == []
    assert elig([make_pattern(status="rejected")]) == []

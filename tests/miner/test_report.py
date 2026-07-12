from types import SimpleNamespace

from pae.miner.report import render_patterns


def _rows():
    return [
        SimpleNamespace(
            kind="time_of_day",
            entity_id="light.bar",
            action="off",
            day_type="weekday",
            trigger_entity_id=None,
            trigger_state=None,
            tod_minutes=1332.0,
            support=1.0,
            confidence=1.0,
            lift=16.0,
            occurrences=5,
            suspected_schedule=False,
        ),
        SimpleNamespace(
            kind="event_pair",
            entity_id="light.den",
            action="on",
            day_type=None,
            trigger_entity_id="media_player.tv",
            trigger_state="playing",
            tod_minutes=None,
            support=0.83,
            confidence=0.83,
            lift=240.0,
            occurrences=5,
            suspected_schedule=False,
        ),
        SimpleNamespace(
            kind="time_of_day",
            entity_id="switch.pool_2",
            action="on",
            day_type="weekday",
            trigger_entity_id=None,
            trigger_state=None,
            tod_minutes=782.0,
            support=1.0,
            confidence=1.0,
            lift=16.0,
            occurrences=5,
            suspected_schedule=True,
        ),
    ]


def test_render_patterns_table():
    out = render_patterns(_rows())
    lines = out.splitlines()
    assert len(lines) == 4  # header + 3 rows
    assert "light.bar -> off ~22:12 (weekday)" in out
    assert "media_player.tv=playing => light.den -> on" in out
    assert "sched" in out  # suspected_schedule flag column


def test_render_patterns_empty():
    assert render_patterns([]) == "no patterns mined yet"

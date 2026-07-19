"""Invariants for the basement tablet dashboard tooling (ha/basement_tablet)."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ha" / "basement_tablet"))

import entities  # noqa: E402


def test_row_tables_are_complete_and_unique():
    assert sorted(entities.ROWS) == [1, 2, 3, 4]
    assert len(entities.ALL_ROW_LIGHTS) == 11
    assert len(set(entities.ALL_ROW_LIGHTS)) == 11
    assert len(entities.ALL_OFF_TARGETS) == 16
    assert set(entities.ZONES.values()) <= set(entities.ALL_OFF_TARGETS)


def test_slider_value_semantics_front_dies_first():
    # value = rows on counting from the rear; row r on iff value >= 5 - r
    assert [entities.row_is_on(4, r) for r in (1, 2, 3, 4)] == [True, True, True, True]
    assert [entities.row_is_on(3, r) for r in (1, 2, 3, 4)] == [False, True, True, True]
    assert [entities.row_is_on(2, r) for r in (1, 2, 3, 4)] == [False, False, True, True]
    assert [entities.row_is_on(1, r) for r in (1, 2, 3, 4)] == [False, False, False, True]
    assert [entities.row_is_on(0, r) for r in (1, 2, 3, 4)] == [False, False, False, False]


def test_value_jinja_mentions_every_row_light_and_rounds_down():
    tmpl = entities.value_jinja()
    for e in entities.ALL_ROW_LIGHTS:
        assert e in tmpl
    # rear-anchored contiguity: the elif chain must test r4 alone last
    assert "{%- elif r4 -%}1" in tmpl
    assert tmpl.strip().endswith("{%- endif -%}")

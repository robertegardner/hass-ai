"""Entity tables for the basement tablet dashboard — single source of truth.

Zones and the can-row map are operator-confirmed; see
docs/superpowers/specs/2026-07-19-basement-tablet-dashboard-design.md.
"""

from __future__ import annotations

INPUT_NUMBER = "input_number.basement_cans_rows"

ZONES = {
    "cans": "light.basement_cans",
    "bar": "light.bar_lights",
    "mancave": "light.mancave",
    "foyer": "light.basement_foyer",  # group created by helpers.py
    "bathroom": "switch.basement_bathroom_lights",
}

FOYER_MEMBERS = ["light.bathroom_foyer_a", "light.bathroom_foyer_b"]

# Can rows front (1) -> rear (4). Sliding the tablet slider down turns rows off
# front to back, so the rear of the room stays lit longest.
ROWS: dict[int, list[str]] = {
    1: ["light.0xc890a81f69ed0000", "light.0xc890a81f577b0000", "light.right_row_1_front"],
    2: ["light.0xc890a81eb5df0000", "light.0xc890a81f6ff30000", "light.right_row_2_3"],
    3: ["light.left_row_3_3", "light.right_row_3_3"],
    4: ["light.left_rear_row_3", "light.0xc890a81f5a530000", "light.0xc890a81ed77e0000"],
}

ALL_ROW_LIGHTS = [e for row in ROWS.values() for e in row]

ALL_OFF_TARGETS = list(ZONES.values()) + ALL_ROW_LIGHTS


def row_is_on(value: int, row: int) -> bool:
    """Slider value -> should this row be lit? Value counts rows on from the rear."""
    return value >= 5 - row


def row_on_jinja(row: int) -> str:
    """Jinja expression: every light in the row is currently on."""
    return " and ".join(f"states('{e}') == 'on'" for e in ROWS[row])


def value_jinja() -> str:
    """Jinja rendering the largest N such that rows (5-N)..4 are all on.

    Mixed / non-conforming states round down. Whitespace-controlled so the
    rendered result is a bare digit input_number.set_value can coerce.
    """
    setters = "".join(f"{{%- set r{r} = {row_on_jinja(r)} -%}}" for r in ROWS)
    return (
        setters
        + "{%- if r1 and r2 and r3 and r4 -%}4"
        + "{%- elif r2 and r3 and r4 -%}3"
        + "{%- elif r3 and r4 -%}2"
        + "{%- elif r4 -%}1"
        + "{%- else -%}0"
        + "{%- endif -%}"
    )

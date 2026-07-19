# Basement Tablet Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A wall-tablet Lovelace dashboard (`basement-tablet`) that replaces the basement wall switches: five zone tiles, an All Off button, and a vertical cans-by-row slider, plus the HA helpers/automations behind it.

**Architecture:** Standalone tooling in `ha/basement_tablet/` mirroring `ha/weather/` (PAE's HA client is read-only enforced — never route writes through it). One entity-table module is the single source of truth; `helpers.py` creates the input_number, foyer light group, and two automations; `build_dashboard.py` generates `dashboard.json`; `deploy_dashboard.py` pushes it over WS. Spec: `docs/superpowers/specs/2026-07-19-basement-tablet-dashboard-design.md`.

**Tech Stack:** Python (stdlib + `websockets` from the `weather` uv group), HA REST config flows + WS storage collections, HACS cards already installed: lovelace-mushroom, my-cards (my-slider-v2), lovelace-layout-card, lovelace-card-mod.

## Global Constraints

- **No write to HA executes without explicit operator confirmation at deploy time** (CLAUDE.md hard rule). All scripts take `--dry-run`; Task 5 is the only task that writes to HA.
- Slider semantics: value = rows on counting from the **rear**; sliding down turns rows off **front to back** (row r is on iff `value >= 5 - r`).
- Row map (operator-confirmed, verbatim entity ids):
  - Row 1 (front): `light.0xc890a81f69ed0000`, `light.0xc890a81f577b0000`, `light.right_row_1_front`
  - Row 2: `light.0xc890a81eb5df0000`, `light.0xc890a81f6ff30000`, `light.right_row_2_3`
  - Row 3: `light.left_row_3_3`, `light.right_row_3_3`
  - Row 4 (rear): `light.left_rear_row_3`, `light.0xc890a81f5a530000`, `light.0xc890a81ed77e0000`
- Zones: cans `light.basement_cans` · bar `light.bar_lights` · mancave `light.mancave` · foyer `light.basement_foyer` (new group of `light.bathroom_foyer_a` + `light.bathroom_foyer_b`) · bathroom `switch.basement_bathroom_lights`.
- Tests must import via `sys.path.insert(0, str(REPO_ROOT / "ha" / "basement_tablet"))` (same pattern as `tests/test_weather_discovery.py`) and must pass under plain `uv run pytest` — so `helpers.py`/`deploy_dashboard.py` must import `websockets` **lazily inside functions**, not at module top.
- Lint: `uv run ruff check src tests scripts` (line length 100). Add `"ha/basement_tablet/build_dashboard.py" = ["E501"]` to ruff per-file-ignores (long CSS strings), like the weather build script.
- The dashboard is generated: never hand-edit in HA. The existing `basement-control` dashboard is untouched.

---

### Task 1: Entity tables + row logic (`entities.py`)

**Files:**
- Create: `ha/basement_tablet/entities.py`
- Test: `tests/test_basement_tablet.py`

**Interfaces:**
- Produces (used by Tasks 2–3):
  - `INPUT_NUMBER: str` = `"input_number.basement_cans_rows"`
  - `ZONES: dict[str, str]` keys `cans|bar|mancave|foyer|bathroom`
  - `FOYER_MEMBERS: list[str]`
  - `ROWS: dict[int, list[str]]` rows 1–4 front→rear
  - `ALL_ROW_LIGHTS: list[str]` (11 entities)
  - `ALL_OFF_TARGETS: list[str]` (5 zones + 11 rows)
  - `row_is_on(value: int, row: int) -> bool`
  - `row_on_jinja(row: int) -> str`
  - `value_jinja() -> str` (whitespace-controlled Jinja, renders `0`–`4`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_basement_tablet.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_basement_tablet.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'entities'`

- [ ] **Step 3: Write `ha/basement_tablet/entities.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_basement_tablet.py -v`
Expected: 3 PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check tests ha/basement_tablet
git add ha/basement_tablet/entities.py tests/test_basement_tablet.py
git commit -m "feat(basement-tablet): entity tables and row-slider semantics"
```

---

### Task 2: Helpers + automations script (`helpers.py`)

**Files:**
- Create: `ha/basement_tablet/helpers.py`
- Modify: `tests/test_basement_tablet.py` (append tests)

**Interfaces:**
- Consumes: everything from `entities.py` (Task 1 signatures).
- Produces:
  - `apply_automation() -> dict` — HA automation config, id `basement_cans_rows_apply`
  - `sync_automation() -> dict` — HA automation config, id `basement_cans_rows_sync`
  - CLI: `uv run --group weather python ha/basement_tablet/helpers.py [--dry-run]`
- HA objects created at deploy time (Task 5): `input_number.basement_cans_rows`, `light.basement_foyer`, both automations.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_basement_tablet.py`:

```python
import helpers  # noqa: E402


def test_apply_automation_maps_thresholds_to_rows():
    auto = helpers.apply_automation()
    assert auto["id"] == "basement_cans_rows_apply"
    assert auto["mode"] == "restart"
    assert auto["trigger"] == [
        {"platform": "state", "entity_id": entities.INPUT_NUMBER}
    ]
    assert len(auto["action"]) == 4
    for action, row in zip(auto["action"], (1, 2, 3, 4)):
        threshold = 5 - row
        cond = action["choose"][0]["conditions"][0]["value_template"]
        assert f">= {threshold}" in cond
        on = action["choose"][0]["sequence"][0]
        off = action["default"][0]
        assert on["service"] == "homeassistant.turn_on"
        assert off["service"] == "homeassistant.turn_off"
        assert on["target"]["entity_id"] == entities.ROWS[row]
        assert off["target"]["entity_id"] == entities.ROWS[row]


def test_sync_automation_debounces_and_covers_all_rows():
    auto = helpers.sync_automation()
    assert auto["id"] == "basement_cans_rows_sync"
    assert auto["mode"] == "restart"  # restart = debounce restarts on each change
    assert auto["trigger"] == [
        {"platform": "state", "entity_id": entities.ALL_ROW_LIGHTS}
    ]
    assert auto["action"][0] == {"delay": {"seconds": 1}}
    set_value = auto["action"][1]
    assert set_value["service"] == "input_number.set_value"
    assert set_value["target"]["entity_id"] == entities.INPUT_NUMBER
    assert set_value["data"]["value"] == entities.value_jinja()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_basement_tablet.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'helpers'`

- [ ] **Step 3: Write `ha/basement_tablet/helpers.py`**

`websockets` is imported lazily (dev test env doesn't install the weather group).
The config-flow driver is the proven one from `ha/weather/helpers.py` (kept standalone
on purpose, same as the weather stack).

```python
"""Create the basement-tablet helpers and automations in HA.

Creates (idempotent):
  - input_number.basement_cans_rows  (0-4 slider; WS storage collection)
  - light.basement_foyer             (light-group config flow over the Hue foyer pair)
  - automation basement_cans_rows_apply  (slider -> rows, front row dies first)
  - automation basement_cans_rows_sync   (rows -> slider, 1 s debounce, mode restart)

Automations POST to fixed ids, so re-running updates them in place. The
apply/sync pair cannot loop: setting an unchanged input_number value emits no
state change.

Usage:
    uv run --group weather python ha/basement_tablet/helpers.py --dry-run
    uv run --group weather python ha/basement_tablet/helpers.py

Needs HA_TOKEN in the environment (source .env first).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request

from entities import ALL_ROW_LIGHTS, FOYER_MEMBERS, INPUT_NUMBER, ROWS, value_jinja

HA_URL = os.environ.get("HA_URL", "http://homeassistant.iot:8123")
HA_WS = os.environ.get("HA_WS_URL", "ws://homeassistant.iot:8123/api/websocket")

INPUT_NUMBER_NAME = "Basement cans rows"  # slugs to basement_cans_rows
FOYER_GROUP_TITLE = "Basement Foyer"      # slugs to light.basement_foyer


def apply_automation() -> dict:
    """Slider value -> can rows. Row r is on iff value >= 5 - r (front dies first)."""
    actions = []
    for row, row_entities in ROWS.items():
        threshold = 5 - row
        actions.append({
            "choose": [{
                "conditions": [{
                    "condition": "template",
                    "value_template": (
                        f"{{{{ states('{INPUT_NUMBER}') | float(0) | int >= {threshold} }}}}"
                    ),
                }],
                "sequence": [{
                    "service": "homeassistant.turn_on",
                    "target": {"entity_id": row_entities},
                }],
            }],
            "default": [{
                "service": "homeassistant.turn_off",
                "target": {"entity_id": row_entities},
            }],
        })
    return {
        "id": "basement_cans_rows_apply",
        "alias": "Basement cans rows — apply slider",
        "description": "Tablet row slider -> can rows; sliding down kills front rows first.",
        "mode": "restart",
        "trigger": [{"platform": "state", "entity_id": INPUT_NUMBER}],
        "condition": [],
        "action": actions,
    }


def sync_automation() -> dict:
    """Row states -> slider. 1 s debounced (mode restart) so mid-flight apply
    runs settle before the slider is recomputed; a no-op set_value ends the cycle."""
    return {
        "id": "basement_cans_rows_sync",
        "alias": "Basement cans rows — sync slider",
        "description": "Keep the tablet row slider truthful when cans change elsewhere.",
        "mode": "restart",
        "trigger": [{"platform": "state", "entity_id": ALL_ROW_LIGHTS}],
        "condition": [],
        "action": [
            {"delay": {"seconds": 1}},
            {
                "service": "input_number.set_value",
                "target": {"entity_id": INPUT_NUMBER},
                "data": {"value": value_jinja()},
            },
        ],
    }


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{HA_URL}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['HA_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or "{}")


async def ensure_input_number(dry_run: bool) -> None:
    import websockets  # lazy: not installed in the dev/test env

    async with websockets.connect(HA_WS, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": os.environ["HA_TOKEN"]}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            sys.exit(f"auth failed: {auth}")

        async def call(msg_id: int, payload: dict):
            await ws.send(json.dumps({"id": msg_id, **payload}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == msg_id and msg.get("type") == "result":
                    if not msg.get("success"):
                        sys.exit(f"HA rejected {payload.get('type')}: {msg.get('error')}")
                    return msg.get("result")

        existing = await call(1, {"type": "input_number/list"})
        if any(item.get("name") == INPUT_NUMBER_NAME for item in existing):
            print(f"skip (exists): input_number '{INPUT_NUMBER_NAME}'")
            return
        spec = {
            "type": "input_number/create",
            "name": INPUT_NUMBER_NAME,
            "min": 0,
            "max": 4,
            "step": 1,
            "mode": "slider",
            "icon": "mdi:spotlight-beam",
        }
        if dry_run:
            print(f"dry-run: would create input_number: {json.dumps(spec)}")
            return
        await call(2, spec)
        print(f"created input_number '{INPUT_NUMBER_NAME}' ({INPUT_NUMBER})")


def existing_titles() -> set[str]:
    entries = api("GET", "/api/config/config_entries/entry")
    return {e["title"] for e in entries}


def drive_flow(helper: dict, dry_run: bool) -> str:
    """Walk one config flow to completion (or abort it in dry-run)."""
    flow = api("POST", "/api/config/config_entries/flow", {"handler": helper["handler"]})
    try:
        for _ in range(6):  # menu -> form(s) -> create_entry
            kind = flow["type"]
            if kind == "menu":
                flow = api(
                    "POST",
                    f"/api/config/config_entries/flow/{flow['flow_id']}",
                    {"next_step_id": helper["menu"]},
                )
            elif kind == "form":
                step = flow["step_id"]
                data = helper["steps"].get(step, {})
                if dry_run:
                    schema = [f"{f.get('name')}{'*' if f.get('required') else ''}"
                              for f in flow.get("data_schema") or []]
                    print(f"  step '{step}' schema: {schema}")
                    print(f"  would submit: {json.dumps(data)[:200]}")
                    return "dry-run ok"
                flow = api(
                    "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", data
                )
                if flow.get("errors"):
                    return f"ERRORS: {flow['errors']}"
            elif kind == "create_entry":
                return "created"
            else:
                return f"unexpected flow type: {kind}"
        return "did not converge"
    finally:
        if dry_run:
            try:
                api("DELETE", f"/api/config/config_entries/flow/{flow['flow_id']}")
            except Exception:
                pass  # flow already finished or expired


FOYER_GROUP_FLOW = {
    "handler": "group",
    "menu": "light",
    "steps": {
        "light": {
            "title": FOYER_GROUP_TITLE,
            "entities": FOYER_MEMBERS,
            "hide_members": False,
        }
    },
}


def ensure_foyer_group(dry_run: bool) -> None:
    if FOYER_GROUP_TITLE in existing_titles():
        print(f"skip (exists): light group '{FOYER_GROUP_TITLE}'")
        return
    print(f"{'dry-run' if dry_run else 'create'}: light group '{FOYER_GROUP_TITLE}'")
    print(f"  -> {drive_flow(FOYER_GROUP_FLOW, dry_run)}")


def ensure_automations(dry_run: bool) -> None:
    for auto in (apply_automation(), sync_automation()):
        if dry_run:
            print(f"dry-run: would POST automation '{auto['id']}':")
            print(json.dumps(auto, indent=2))
            continue
        api("POST", f"/api/config/automation/config/{auto['id']}", auto)
        print(f"wrote automation '{auto['id']}' (created or updated in place)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if "HA_TOKEN" not in os.environ:
        sys.exit("HA_TOKEN not set (source .env first)")

    asyncio.run(ensure_input_number(args.dry_run))
    ensure_foyer_group(args.dry_run)
    ensure_automations(args.dry_run)


if __name__ == "__main__":
    main()
```

Note: the group flow's `light` step field name (`title` vs `name`) varies by HA
version — the `--dry-run` in Task 5 prints the live schema; if it shows `name*`,
change the key in `FOYER_GROUP_FLOW` before the real run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_basement_tablet.py -v`
Expected: 5 PASS (imports work without websockets installed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check tests ha/basement_tablet
git add ha/basement_tablet/helpers.py tests/test_basement_tablet.py
git commit -m "feat(basement-tablet): helpers script — input_number, foyer group, row automations"
```

---

### Task 3: Dashboard generator (`build_dashboard.py`)

**Files:**
- Create: `ha/basement_tablet/build_dashboard.py` (writes `ha/basement_tablet/dashboard.json`)
- Modify: `tests/test_basement_tablet.py` (append tests), `pyproject.toml` (ruff E501 ignore)

**Interfaces:**
- Consumes: `ZONES`, `ALL_OFF_TARGETS`, `INPUT_NUMBER` from `entities.py`.
- Produces: `build() -> dict` (full Lovelace config) and `dashboard.json` on disk; Task 4's deploy script reads that file verbatim.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_basement_tablet.py`:

```python
import build_dashboard  # noqa: E402


def _cards():
    cfg = build_dashboard.build()
    assert len(cfg["views"]) == 1
    return cfg["views"][0]["cards"]


def test_dashboard_has_all_zone_tiles_and_grid_areas():
    cards = _cards()
    by_area = {c["view_layout"]["grid-area"]: c for c in cards}
    assert set(by_area) == {"cans", "bar", "mancave", "foyer", "bath", "alloff", "rows"}
    for zone, area in [("cans", "cans"), ("bar", "bar"), ("mancave", "mancave"),
                       ("foyer", "foyer")]:
        card = by_area[area]
        assert card["type"] == "custom:mushroom-light-card"
        assert card["entity"] == entities.ZONES[zone]
        assert card["show_brightness_control"] is True
        assert card["tap_action"] == {"action": "toggle"}
    bath = by_area["bath"]
    assert bath["type"] == "custom:mushroom-entity-card"
    assert bath["entity"] == entities.ZONES["bathroom"]
    assert bath["tap_action"] == {"action": "toggle"}


def test_all_off_targets_every_zone_and_row():
    cards = _cards()
    alloff = next(c for c in cards if c["view_layout"]["grid-area"] == "alloff")
    tap = alloff["tap_action"]
    assert tap["action"] == "call-service"
    assert tap["service"] == "homeassistant.turn_off"
    assert sorted(tap["target"]["entity_id"]) == sorted(entities.ALL_OFF_TARGETS)


def test_rows_slider_bound_to_input_number():
    cards = _cards()
    col = next(c for c in cards if c["view_layout"]["grid-area"] == "rows")
    slider = col["cards"][-1]
    assert slider["type"] == "custom:my-slider-v2"
    assert slider["entity"] == entities.INPUT_NUMBER
    assert slider["vertical"] is True
    assert slider["flipped"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_basement_tablet.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'build_dashboard'`

- [ ] **Step 3: Write `ha/basement_tablet/build_dashboard.py`**

```python
"""Generate the basement tablet dashboard config (dashboard.json).

Design: "switch panel at night" — charcoal glass, six oversized rounded tiles,
warm amber glow on whatever is lit, one tall cans-by-row slider column. Built
for a 10" landscape wall tablet replacing the physical switches: tap = toggle,
drag a tile = dim, no header, no chrome.

Never hand-edit the deployed dashboard — change this generator, rebuild, redeploy:

    uv run --group weather python ha/basement_tablet/build_dashboard.py
    uv run --group weather python ha/basement_tablet/deploy_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

from entities import ALL_OFF_TARGETS, INPUT_NUMBER, ZONES

# ---------------------------------------------------------------- tokens

BG_VIEW = (
    "radial-gradient(ellipse 130% 90% at 50% -20%, "
    "#1b1e24 0%, #101216 55%, #0a0b0e 100%) fixed"
)
TILE_BG = "rgba(255,255,255,0.045)"
TILE_EDGE = "rgba(255,255,255,0.07)"
AMBER = "255, 179, 84"          # accent: anything lit glows warm amber
OFF_ICON = "rgba(238,235,230,0.35)"
TEXT_HI = "#f2f0ec"
TEXT_LO = "rgba(242,240,236,0.55)"
RADIUS = "28px"

TILE_MOD = f"""
ha-card {{
  height: 100%;
  box-sizing: border-box;
  border-radius: {RADIUS};
  background: {TILE_BG};
  border: 1px solid {TILE_EDGE};
  padding: 22px;
  --icon-size: 58px;
  --card-primary-font-size: 26px;
  --card-primary-font-weight: 600;
  --card-secondary-font-size: 16px;
  --primary-text-color: {TEXT_HI};
  --secondary-text-color: {TEXT_LO};
  --rgb-state-light: {AMBER};
  --rgb-state-switch: {AMBER};
  --rgb-disabled: 120, 120, 120;
}}
"""


def light_tile(entity: str, name: str, icon: str, area: str) -> dict:
    return {
        "type": "custom:mushroom-light-card",
        "entity": entity,
        "name": name,
        "icon": icon,
        "layout": "vertical",
        "fill_container": True,
        "show_brightness_control": True,
        "use_light_color": False,
        "collapsible_controls": False,
        "tap_action": {"action": "toggle"},
        "hold_action": {"action": "none"},
        "double_tap_action": {"action": "none"},
        "view_layout": {"grid-area": area},
        "card_mod": {"style": TILE_MOD},
    }


def switch_tile(entity: str, name: str, icon: str, area: str) -> dict:
    return {
        "type": "custom:mushroom-entity-card",
        "entity": entity,
        "name": name,
        "icon": icon,
        "layout": "vertical",
        "fill_container": True,
        "tap_action": {"action": "toggle"},
        "hold_action": {"action": "none"},
        "double_tap_action": {"action": "none"},
        "view_layout": {"grid-area": area},
        "card_mod": {"style": TILE_MOD},
    }


def all_off_tile(area: str) -> dict:
    return {
        "type": "custom:mushroom-template-card",
        "primary": "All Off",
        "secondary": "everything downstairs",
        "icon": "mdi:power",
        "icon_color": "red",
        "layout": "vertical",
        "fill_container": True,
        "tap_action": {
            "action": "call-service",
            "service": "homeassistant.turn_off",
            "target": {"entity_id": ALL_OFF_TARGETS},
        },
        "hold_action": {"action": "none"},
        "double_tap_action": {"action": "none"},
        "view_layout": {"grid-area": area},
        "card_mod": {"style": TILE_MOD},
    }


def rows_column(area: str) -> dict:
    """Label + tall vertical slider. 4 = all rows; sliding down turns rows off
    front to back (rear row survives longest)."""
    label = {
        "type": "custom:mushroom-template-card",
        "primary": "Cans by Row",
        "secondary": "slide down · front rows off first",
        "icon": "mdi:spotlight-beam",
        "tap_action": {"action": "none"},
        "card_mod": {"style": f"""
ha-card {{
  background: none;
  border: none;
  box-shadow: none;
  --card-primary-font-size: 20px;
  --card-secondary-font-size: 13px;
  --primary-text-color: {TEXT_HI};
  --secondary-text-color: {TEXT_LO};
  --icon-size: 40px;
}}
"""},
    }
    slider = {
        "type": "custom:my-slider-v2",
        "entity": INPUT_NUMBER,
        "vertical": True,
        "flipped": True,   # full (4) at the top
        "step": 1,
        "styles": {
            "card": [
                {"height": "calc(100vh - 200px)"},
                {"width": "100%"},
                {"border-radius": RADIUS},
                {"background": TILE_BG},
                {"border": f"1px solid {TILE_EDGE}"},
            ],
            "container": [{"border-radius": RADIUS}],
            "track": [{"background": "rgba(255,255,255,0.06)"}],
            "progress": [{
                "background": (
                    f"linear-gradient(180deg, rgba({AMBER}, 0.95) 0%, "
                    f"rgba({AMBER}, 0.40) 100%)"
                )
            }],
            "thumb": [{"background": TEXT_HI}],
        },
    }
    return {
        "type": "vertical-stack",
        "view_layout": {"grid-area": area},
        "cards": [label, slider],
    }


def build() -> dict:
    cards = [
        light_tile(ZONES["cans"], "Basement Cans", "mdi:spotlight-beam", "cans"),
        light_tile(ZONES["bar"], "Bar Lights", "mdi:glass-cocktail", "bar"),
        light_tile(ZONES["mancave"], "ManCave", "mdi:sofa", "mancave"),
        light_tile(ZONES["foyer"], "Foyer", "mdi:coach-lamp", "foyer"),
        switch_tile(ZONES["bathroom"], "Bathroom", "mdi:shower", "bath"),
        all_off_tile("alloff"),
        rows_column("rows"),
    ]
    view = {
        "title": "Basement",
        "path": "main",
        "type": "custom:grid-layout",
        "background": BG_VIEW,
        "badges": [],
        "layout": {
            "grid-template-columns": "1fr 1fr minmax(240px, 22%)",
            "grid-template-rows": "1fr 1fr 1fr",
            "grid-template-areas": (
                '"cans bar rows" "mancave foyer rows" "bath alloff rows"'
            ),
            "grid-gap": "18px",
            "padding": "18px",
            "height": "100%",
            "mediaquery": {
                # portrait fallback: single column, slider last
                "(orientation: portrait)": {
                    "grid-template-columns": "1fr",
                    "grid-template-rows": "repeat(6, 140px) 1fr",
                    "grid-template-areas": (
                        '"cans" "bar" "mancave" "foyer" "bath" "alloff" "rows"'
                    ),
                }
            },
        },
        "cards": cards,
    }
    return {"title": "Basement", "views": [view]}


if __name__ == "__main__":
    out = Path(__file__).parent / "dashboard.json"
    config = build()
    out.write_text(json.dumps(config, indent=1) + "\n")
    cards = config["views"][0]["cards"]
    print(f"wrote {out} ({len(cards)} top-level cards)")
```

- [ ] **Step 4: Add the ruff ignore**

In `pyproject.toml`, next to the existing `"ha/weather/build_dashboard.py" = ["E501"]`
per-file-ignore, add:

```toml
"ha/basement_tablet/build_dashboard.py" = ["E501"]
```

- [ ] **Step 5: Run tests + build + lint**

```bash
uv run pytest tests/test_basement_tablet.py -v          # expected: 8 PASS
uv run python ha/basement_tablet/build_dashboard.py     # writes dashboard.json
uv run ruff check tests ha/basement_tablet
```

- [ ] **Step 6: Commit**

```bash
git add ha/basement_tablet/build_dashboard.py ha/basement_tablet/dashboard.json \
        tests/test_basement_tablet.py pyproject.toml
git commit -m "feat(basement-tablet): dashboard generator — tile grid + row slider"
```

---

### Task 4: Deploy script + README

**Files:**
- Create: `ha/basement_tablet/deploy_dashboard.py`
- Create: `ha/basement_tablet/README.md`

**Interfaces:**
- Consumes: `ha/basement_tablet/dashboard.json` (Task 3 output).
- Produces: CLI `uv run --group weather python ha/basement_tablet/deploy_dashboard.py [--dry-run]`; creates/updates dashboard `basement-tablet`.

- [ ] **Step 1: Write `ha/basement_tablet/deploy_dashboard.py`**

Same shape as `ha/weather/deploy_dashboard.py`, plus `--dry-run` and lazy websockets:

```python
"""Create/update the "Basement" tablet dashboard in HA from dashboard.json.

Standalone websocket client on purpose — PAE's HA client is read-only enforced
and must not be widened for this.

Usage:
    uv run --group weather python ha/basement_tablet/deploy_dashboard.py --dry-run
    uv run --group weather python ha/basement_tablet/deploy_dashboard.py

Needs HA_TOKEN in the environment (source .env first).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HA_WS = os.environ.get("HA_WS_URL", "ws://homeassistant.iot:8123/api/websocket")
URL_PATH = "basement-tablet"


async def call(ws, msg_id: int, payload: dict) -> dict:
    await ws.send(json.dumps({"id": msg_id, **payload}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == msg_id and msg.get("type") == "result":
            if not msg.get("success"):
                sys.exit(f"HA rejected {payload.get('type')}: {msg.get('error')}")
            return msg.get("result")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import websockets  # lazy: not installed in the dev/test env

    config = json.loads((Path(__file__).parent / "dashboard.json").read_text())
    n_cards = sum(len(v["cards"]) for v in config["views"])

    async with websockets.connect(HA_WS, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": os.environ["HA_TOKEN"]}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            sys.exit(f"auth failed: {auth}")

        dashboards = await call(ws, 1, {"type": "lovelace/dashboards/list"})
        existing = next((d for d in dashboards if d.get("url_path") == URL_PATH), None)

        if args.dry_run:
            action = "update config of" if existing else "create dashboard and set config of"
            print(f"dry-run: would {action} /{URL_PATH} "
                  f"({len(config['views'])} views, {n_cards} cards)")
            return

        if existing is None:
            await call(ws, 2, {
                "type": "lovelace/dashboards/create",
                "url_path": URL_PATH,
                "title": "Basement",
                "icon": "mdi:lightbulb-group",
                "show_in_sidebar": True,
                "require_admin": False,
            })
            print(f"created dashboard /{URL_PATH}")
        else:
            print(f"dashboard /{URL_PATH} exists — updating config in place")

        await call(ws, 3, {
            "type": "lovelace/config/save",
            "url_path": URL_PATH,
            "config": config,
        })
        print(f"saved config: {len(config['views'])} views, {n_cards} cards")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Write `ha/basement_tablet/README.md`**

```markdown
# Basement tablet — wall-switch replacement dashboard

Tooling for the `basement-tablet` Lovelace dashboard: five zone tiles
(tap = toggle, drag = dim), All Off, and a vertical cans-by-row slider
(4 = all rows; sliding down turns rows off front to back). Standalone from the
PAE package on purpose: PAE's HA client is read-only enforced.

Spec: `docs/superpowers/specs/2026-07-19-basement-tablet-dashboard-design.md`.
Entity/row tables live in `entities.py` — the single source of truth for
helpers, automations, and the dashboard.

## Order of operations

```bash
set -a && . ./.env && set +a    # load HA_TOKEN first

# 1. Helpers + automations (input_number, foyer group, apply/sync automations)
uv run --group weather python ha/basement_tablet/helpers.py --dry-run   # review
uv run --group weather python ha/basement_tablet/helpers.py             # create

# 2. Dashboard (HACS cards required: lovelace-mushroom, my-cards,
#    lovelace-layout-card, lovelace-card-mod — all already installed)
uv run --group weather python ha/basement_tablet/build_dashboard.py     # writes dashboard.json
uv run --group weather python ha/basement_tablet/deploy_dashboard.py --dry-run
uv run --group weather python ha/basement_tablet/deploy_dashboard.py    # create/update
```

Every non-dry-run step writes to HA — operator confirmation required
(CLAUDE.md hard rule). `--dry-run` variants are always safe.

## Facts that shaped the design

- Foyer = `light.basement_foyer`, a group over `light.bathroom_foyer_a/b`. Those
  Hue lights are `unavailable` until the old ISY load switch is replaced; the
  tile renders greyed until then and needs no change after.
- The apply/sync automation pair cannot loop: apply is idempotent and sync's
  `input_number.set_value` with an unchanged value emits no state change. Sync
  debounces 1 s (`mode: restart`) so apply's row-by-row writes settle first.
- Mixed row states round the slider **down** (value = rows contiguously on from
  the rear).
- All Off targets the 5 zones **plus** all 11 row lights explicitly — belt and
  suspenders in case `light.basement_cans` doesn't cover every row.
- The dashboard is generated: edit `build_dashboard.py`, rebuild, redeploy.
  Never hand-edit in HA.

## Tablet URL

`http://homeassistant.iot:8123/basement-tablet/main`
```

- [ ] **Step 3: Lint + full test suite**

```bash
uv run ruff check src tests scripts ha/basement_tablet
uv run pytest
```
Expected: all tests pass (the new file adds 8).

- [ ] **Step 4: Commit**

```bash
git add ha/basement_tablet/deploy_dashboard.py ha/basement_tablet/README.md
git commit -m "feat(basement-tablet): deploy script and README"
```

---

### Task 5: Deploy to HA (OPERATOR-GATED) + live verification

**Files:** none (runtime only).

**Interfaces:**
- Consumes: all CLIs from Tasks 2–4.

**Every non-dry-run step below writes to HA. Show the operator the dry-run
output and get explicit confirmation before each real run. Do not proceed past
a failed step.**

- [ ] **Step 1: Dry-run everything, present output to operator**

```bash
set -a && . ./.env && set +a
uv run --group weather python ha/basement_tablet/helpers.py --dry-run
uv run --group weather python ha/basement_tablet/build_dashboard.py
uv run --group weather python ha/basement_tablet/deploy_dashboard.py --dry-run
```
Check the group flow's printed `light` step schema: if it lists `name*` instead
of `title`, fix `FOYER_GROUP_FLOW` in `helpers.py` first (see Task 2 note).

- [ ] **Step 2: WAIT for operator confirmation** (hard rule — no HA writes without it)

- [ ] **Step 3: Create helpers + automations**

Run: `uv run --group weather python ha/basement_tablet/helpers.py`
Expected: input_number + group + 2 automations reported created. Verify:

```bash
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://homeassistant.iot:8123/api/states/input_number.basement_cans_rows | head -c 300
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://homeassistant.iot:8123/api/states/light.basement_foyer | head -c 300
```
Expected: both return states (foyer likely `unavailable` until the switch swap — fine).

- [ ] **Step 4: Deploy dashboard**

Run: `uv run --group weather python ha/basement_tablet/deploy_dashboard.py`
Expected: `created dashboard /basement-tablet` + `saved config: 1 views, 7 cards`

- [ ] **Step 5: Live verification with the operator**

- Slider walk: set `input_number.basement_cans_rows` 4→3→2→1→0 from the dashboard;
  rows die front→back; back up 0→4 they relight rear-first.
- External change: toggle a row from the Cans tile / Z2M; after ~1 s the slider
  snaps to the rounded-down value.
- Each tile toggles its zone; drag dims (cans/bar/mancave; foyer once available).
- All Off from a mixed state: everything off, slider at 0.
- On the tablet itself: layout fills the screen, touch targets comfortable.
- If `my-slider-v2` rejects the `input_number` entity (renders empty): fallback is
  a `custom:mushroom-number-card` rotated vertical via card-mod
  (`transform: rotate(270deg)`) — implement in `rows_column()`, rebuild, redeploy.

- [ ] **Step 6: Final commit + update memory/docs if anything drifted from plan**

```bash
git status   # dashboard.json may have rebuilt; commit any drift
```

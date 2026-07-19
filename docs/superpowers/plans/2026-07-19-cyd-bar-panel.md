# CYD Bar Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ESPHome + LVGL firmware YAML for an ESP32-2432S028R (classic 2.8" CYD, portrait) bar-side panel: Bar toggle, Cans toggle, horizontal bar dimmer, All Off — matching the basement tablet's charcoal/amber look and its exact All Off entity list.

**Architecture:** One self-contained ESPHome YAML (`ha/cyd_bar/basement-cyd-bar.yaml`) the operator pastes into the HA ESPHome add-on (which builds and flashes it). The repo copy is the source of record; a pytest sync-guard parses the YAML and asserts its entity references match `ha/basement_tablet/entities.py`, so tablet-side changes can't silently drift.

**Tech Stack:** ESPHome ≥ 2024.8 (native `lvgl` component; add-on is current so this is met), ILI9341 via `ili9xxx`, XPT2046 touch, pytest + pyyaml (both already project deps).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-cyd-bar-panel-design.md`.
- No secrets in the repo — wifi/API/OTA/AP credentials are `!secret` refs resolved by the add-on's `secrets.yaml`.
- Entity ids verbatim from `entities.py` — including the hex ids like `light.0xc890a81f69ed0000`.
- All Off target order = `list(ZONES.values()) + ALL_ROW_LIGHTS` (16 entities: 5 zones then rows 1→4).
- `homeassistant.action` `data:` values must be strings, not YAML lists — the All Off entity list is a single comma-separated string (HA accepts CSV `entity_id`).
- Ruff line length 100 for Python; run `uv run ruff check src tests scripts` before each commit.
- Operator hard rule: no HA writes from this plan — building/flashing happens in the add-on, by the operator.

---

### Task 1: Sync-guard tests (failing)

**Files:**
- Test: `tests/test_cyd_bar.py`

**Interfaces:**
- Consumes: `ALL_OFF_TARGETS`, `ZONES` from `ha/basement_tablet/entities.py` (import via `sys.path` insertion, same pattern as `tests/test_basement_tablet.py`).
- Produces: the contract Task 2's YAML must satisfy — one `homeassistant.turn_off` action whose CSV `entity_id` equals `ALL_OFF_TARGETS`; imported HA entities exactly `{ZONES["bar"], ZONES["cans"]}`; toggles target exactly those two; `light.turn_on` (dimmer) targets only `ZONES["bar"]`.

- [ ] **Step 1: Write the failing tests**

Note the custom loader: ESPHome YAML uses `!secret` / `!lambda` tags that `yaml.safe_load` rejects; the loader maps unknown tags to `None`. `_walk` recursively yields every dict so tests don't depend on the YAML's exact nesting.

```python
"""Sync guard: the CYD bar panel YAML must track the tablet's entity tables."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha" / "basement_tablet"))

from entities import ALL_OFF_TARGETS, ZONES  # noqa: E402

YAML_PATH = Path(__file__).resolve().parents[1] / "ha" / "cyd_bar" / "basement-cyd-bar.yaml"


class _EsphomeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates ESPHome's !secret / !lambda tags."""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _load():
    return yaml.load(YAML_PATH.read_text(), Loader=_EsphomeLoader)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _service_calls(config, service):
    return [d for d in _walk(config) if d.get("action") == service]


def test_all_off_matches_tablet():
    calls = _service_calls(_load(), "homeassistant.turn_off")
    assert len(calls) == 1
    targets = [e.strip() for e in calls[0]["data"]["entity_id"].split(",")]
    assert targets == ALL_OFF_TARGETS


def test_imported_entities_are_tablet_zones():
    imported = {
        d["entity_id"]
        for d in _walk(_load())
        if d.get("platform") == "homeassistant" and "entity_id" in d
    }
    assert imported == {ZONES["bar"], ZONES["cans"]}


def test_toggle_and_dim_target_tablet_zones():
    cfg = _load()
    toggles = {c["data"]["entity_id"] for c in _service_calls(cfg, "homeassistant.toggle")}
    assert toggles == {ZONES["bar"], ZONES["cans"]}
    dims = {c["data"]["entity_id"] for c in _service_calls(cfg, "light.turn_on")}
    assert dims == {ZONES["bar"]}
```

- [ ] **Step 2: Run tests to verify they fail on the missing YAML**

Run: `uv run pytest tests/test_cyd_bar.py -v`
Expected: 3 FAILED/ERROR with `FileNotFoundError: ... ha/cyd_bar/basement-cyd-bar.yaml`

Do NOT commit yet — the commit lands with Task 2 when the suite is green.

---

### Task 2: The ESPHome YAML

**Files:**
- Create: `ha/cyd_bar/basement-cyd-bar.yaml`

**Interfaces:**
- Consumes: the Task 1 contract (action names, CSV All Off string, imported entities).
- Produces: the complete paste-ready firmware config.

- [ ] **Step 1: Write the YAML**

Design notes baked in below: portrait is the ILI9341's native orientation (no transform); no PSRAM → `buffer_size: 25%`; `slider_pressed` global suppresses HA→device slider echo mid-drag; wake-from-pause resumes on `on_release` so the waking touch can never fire a widget; tiles are text-only (no MDI font upload to manage in the add-on).

```yaml
# Basement CYD bar panel — ESP32-2432S028R (classic 2.8" Cheap Yellow Display),
# mounted PORTRAIT (ILI9341 native 240x320, no rotation needed).
#
# Built and flashed in the HA ESPHome add-on; this repo copy is the source of
# record. Sync guard: tests/test_cyd_bar.py asserts every entity reference below
# matches ha/basement_tablet/entities.py (edit there first, then re-paste here).
#
# Spec: docs/superpowers/specs/2026-07-19-cyd-bar-panel-design.md

esphome:
  name: basement-cyd-bar
  friendly_name: Basement Bar Panel

esp32:
  board: esp32dev
  framework:
    type: esp-idf

logger:

api:
  encryption:
    key: !secret cyd_bar_api_key
  on_client_connected:
    - lvgl.widget.hide: status_dot
  on_client_disconnected:
    - lvgl.widget.show: status_dot

ota:
  - platform: esphome
    password: !secret cyd_bar_ota_password

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap:
    ssid: "Basement-CYD-Bar"
    password: !secret cyd_bar_ap_password

captive_portal:

globals:
  - id: slider_pressed
    type: bool
    initial_value: 'false'

spi:
  - id: spi_tft
    clk_pin: GPIO14
    mosi_pin: GPIO13
    miso_pin: GPIO12
  - id: spi_touch
    clk_pin: GPIO25
    mosi_pin: GPIO32
    miso_pin: GPIO39

display:
  - platform: ili9xxx
    id: tft
    model: ILI9341
    spi_id: spi_tft
    cs_pin: GPIO15
    dc_pin: GPIO2
    invert_colors: false
    auto_clear_enabled: false
    update_interval: never

touchscreen:
  - platform: xpt2046
    id: touch
    spi_id: spi_touch
    cs_pin: GPIO33
    interrupt_pin: GPIO36
    update_interval: 50ms
    threshold: 400
    calibration:
      x_min: 280
      x_max: 3860
      y_min: 280
      y_max: 3860
    on_press:
      - light.turn_on:
          id: backlight
          brightness: 100%
    on_release:
      - if:
          condition: lvgl.is_paused
          then:
            - lvgl.resume:
            - lvgl.widget.redraw:

output:
  - platform: ledc
    pin: GPIO21
    id: backlight_pwm

light:
  - platform: monochromatic
    id: backlight
    output: backlight_pwm
    restore_mode: ALWAYS_ON
    internal: true

font:
  - file: gfonts://Roboto@500
    id: font_tile
    size: 22
    glyphs: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789% "
  - file: gfonts://Roboto@400
    id: font_small
    size: 15
    glyphs: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789% "

binary_sensor:
  - platform: homeassistant
    id: bar_on
    entity_id: light.bar_lights
    on_state:
      - lvgl.widget.update:
          id: bar_tile
          state:
            checked: !lambda return x;
      - if:
          condition:
            lambda: 'return !x && !id(slider_pressed);'
          then:
            - lvgl.slider.update:
                id: bar_slider
                value: 0
            - lvgl.label.update:
                id: bar_pct
                text: "0%"
  - platform: homeassistant
    id: cans_on
    entity_id: light.basement_cans
    on_state:
      - lvgl.widget.update:
          id: cans_tile
          state:
            checked: !lambda return x;

sensor:
  - platform: homeassistant
    id: bar_brightness
    entity_id: light.bar_lights
    attribute: brightness
    on_value:
      - if:
          condition:
            lambda: 'return !id(slider_pressed);'
          then:
            - lvgl.slider.update:
                id: bar_slider
                value: !lambda 'return isnan(x) ? 0 : (int) round(x / 2.55);'
            - lvgl.label.update:
                id: bar_pct
                text:
                  format: "%d%%"
                  args: ['isnan(x) ? 0 : (int) round(x / 2.55)']

lvgl:
  displays: [tft]
  touchscreens: [touch]
  buffer_size: 25%
  on_idle:
    - timeout: 30s
      then:
        - light.turn_on:
            id: backlight
            brightness: 30%
    - timeout: 5min
      then:
        - light.turn_off: backlight
        - lvgl.pause:
  theme:
    button:
      radius: 16
      bg_color: 0x1A1D23
      border_width: 1
      border_color: 0x2A2D33
      text_color: 0xF2F0EC
  pages:
    - id: main_page
      bg_color: 0x101216
      widgets:
        - obj:
            id: status_dot
            x: 114
            y: 4
            width: 12
            height: 12
            radius: 6
            bg_color: 0xE25555
            border_width: 0
        - button:
            id: bar_tile
            x: 8
            y: 20
            width: 109
            height: 108
            checkable: true
            checked:
              bg_color: 0x33270F
              border_color: 0xFFB354
              text_color: 0xFFB354
            widgets:
              - label:
                  align: CENTER
                  text: "BAR"
                  text_font: font_tile
            on_click:
              - homeassistant.action:
                  action: homeassistant.toggle
                  data:
                    entity_id: light.bar_lights
        - button:
            id: cans_tile
            x: 123
            y: 20
            width: 109
            height: 108
            checkable: true
            checked:
              bg_color: 0x33270F
              border_color: 0xFFB354
              text_color: 0xFFB354
            widgets:
              - label:
                  align: CENTER
                  text: "CANS"
                  text_font: font_tile
            on_click:
              - homeassistant.action:
                  action: homeassistant.toggle
                  data:
                    entity_id: light.basement_cans
        - label:
            x: 12
            y: 140
            text: "Bar Dimmer"
            text_font: font_small
            text_color: 0x8B8985
        - label:
            id: bar_pct
            x: 185
            y: 140
            text: "0%"
            text_font: font_small
            text_color: 0xF2F0EC
        - slider:
            id: bar_slider
            x: 12
            y: 166
            width: 216
            height: 48
            min_value: 0
            max_value: 100
            bg_color: 0x1A1D23
            indicator:
              bg_color: 0xFFB354
            knob:
              bg_color: 0xF2F0EC
            on_press:
              - globals.set:
                  id: slider_pressed
                  value: 'true'
            on_value:
              - lvgl.label.update:
                  id: bar_pct
                  text:
                    format: "%d%%"
                    args: ['(int) x']
            on_release:
              - globals.set:
                  id: slider_pressed
                  value: 'false'
              - if:
                  condition:
                    lambda: 'return x > 0;'
                  then:
                    - homeassistant.action:
                        action: light.turn_on
                        data:
                          entity_id: light.bar_lights
                          brightness_pct: !lambda 'return (int) x;'
                  else:
                    - homeassistant.action:
                        action: light.turn_off
                        data:
                          entity_id: light.bar_lights
        - button:
            id: all_off_btn
            x: 8
            y: 260
            width: 224
            height: 52
            bg_color: 0x241014
            border_color: 0xE25555
            widgets:
              - label:
                  align: CENTER
                  text: "ALL OFF"
                  text_font: font_tile
                  text_color: 0xE25555
            on_click:
              - homeassistant.action:
                  action: homeassistant.turn_off
                  data:
                    entity_id: >-
                      light.basement_cans, light.bar_lights,
                      light.game_room_ceiling, light.basement_foyer,
                      switch.basement_bathroom_lights,
                      light.0xc890a81f69ed0000, light.0xc890a81f577b0000,
                      light.right_row_1_front,
                      light.0xc890a81eb5df0000, light.0xc890a81f6ff30000,
                      light.right_row_2_3,
                      light.left_row_3_3, light.right_row_3_3,
                      light.left_rear_row_3, light.0xc890a81f5a530000,
                      light.0xc890a81ed77e0000
```

- [ ] **Step 2: Run the sync tests**

Run: `uv run pytest tests/test_cyd_bar.py -v`
Expected: 3 PASSED. If `test_all_off_matches_tablet` fails on ordering, fix the YAML CSV to `list(ZONES.values()) + ALL_ROW_LIGHTS` order — never reorder the test.

- [ ] **Step 3: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check src tests scripts`
Expected: all pass (live-HA tests auto-excluded).

- [ ] **Step 4: Commit**

```bash
git add tests/test_cyd_bar.py ha/cyd_bar/basement-cyd-bar.yaml
git commit -m "feat(cyd-bar): ESPHome+LVGL firmware YAML with tablet sync guard"
```

---

### Task 3: README (flash + HA setup + troubleshooting)

**Files:**
- Create: `ha/cyd_bar/README.md`

**Interfaces:**
- Consumes: the Task 2 YAML (file name, secret keys referenced).
- Produces: operator-facing setup doc; nothing downstream.

- [ ] **Step 1: Write the README**

```markdown
# CYD Bar Panel (basement-cyd-bar)

ESP32-2432S028R ("Cheap Yellow Display", classic 2.8" resistive) mounted portrait at
the basement bar. Bar toggle, Cans toggle, horizontal bar dimmer, All Off — same
entities and look as the wall tablet (`ha/basement_tablet/`).

Spec: `docs/superpowers/specs/2026-07-19-cyd-bar-panel-design.md`

## Source of record

`basement-cyd-bar.yaml` in this directory is canonical. It is **built and flashed in
the HA ESPHome add-on** — after any edit here, re-paste the whole file into the
add-on's editor and install. `tests/test_cyd_bar.py` fails if the entity references
drift from `ha/basement_tablet/entities.py`; when it fails, fix the YAML, then re-paste.

## First-time setup

1. Add to the add-on's `secrets.yaml` (wifi_ssid/wifi_password usually exist already):
   - `cyd_bar_api_key` — 32-byte base64 key (the add-on's "generate" button, or
     `openssl rand -base64 32`)
   - `cyd_bar_ota_password`, `cyd_bar_ap_password` — any strong strings
2. ESPHome add-on → New Device → skip wizard → paste `basement-cyd-bar.yaml`.
3. First flash over USB: Install → "Plug into this computer" (browser Web Serial),
   board in download mode if needed (hold BOOT while plugging in). Later updates go OTA.
4. HA will discover the device; adopt it in Settings → Devices & Services → ESPHome.
5. **Required:** in the ESPHome integration entry for this device, enable
   **"Allow the device to make Home Assistant actions"** — without it every tap is
   silently dropped.

## Troubleshooting

- **Colors inverted** (charcoal shows as white): set `invert_colors: true` on the
  display block. Some 2432S028R batches (notably the 2-USB variant) need it.
- **Red/blue swapped**: add `color_order: bgr` (or `rgb`, whichever fixes it) to the
  display block.
- **Touch misaligned**: enable `logger` DEBUG for `touchscreen`, tap the four corners,
  and adjust `calibration:` x/y min/max to the logged raw values. If an axis is
  reversed, add `transform: { mirror_x: true }` (or `mirror_y`) to the touchscreen.
- **Panel dead but backlit**: red dot top-center = HA API disconnected (HA restarting,
  wifi drop). It auto-reconnects; if the dot never clears, check step 5 above and the
  device logs in the add-on.

## On-device verification (after each flash)

- [ ] BAR / CANS tiles toggle their lights both directions; amber when on
- [ ] External change (tablet, Hue app) updates tiles and slider within ~1 s
- [ ] Dimmer: drag → % label tracks; release >0 sets brightness; release at 0 turns off
- [ ] Slider doesn't jump while your finger is on it during external changes
- [ ] ALL OFF kills all 16 entities (same list as the tablet)
- [ ] 30 s idle dims backlight; 5 min blanks it; wake touch lights the screen without
      firing any button
- [ ] Restart HA: red dot appears, then clears; controls work after reconnect
```

- [ ] **Step 2: Commit**

```bash
git add ha/cyd_bar/README.md
git commit -m "docs(cyd-bar): setup, flash, and verification README"
```

---

## Self-review notes

- Spec coverage: hardware/pins → Task 2 display/touch/backlight blocks; UI widgets → Task 2 lvgl page; slider behavior incl. mid-drag suppression → `slider_pressed` global + sensor guard; All Off parity → Task 1 test + Task 2 CSV; status dot → api on_client_(dis)connected; idle/wake → `on_idle` + touchscreen `on_release` resume; sync tradeoff + tests → Task 1; README/manual verification → Task 3. Icons from the spec sketch are intentionally text-only labels (no MDI font file to manage in the add-on) — deviation noted to operator.
- All Off count is 16 (5 zones + 11 row lights) — earlier chat said 17; 16 is correct from `entities.py`.
- Type consistency: ids `bar_tile`/`cans_tile`/`bar_slider`/`bar_pct`/`status_dot`/`backlight` used consistently across Task 1 contract, Task 2 YAML, Task 3 README.

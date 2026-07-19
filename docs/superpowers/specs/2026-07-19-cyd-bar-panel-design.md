# CYD Bar Panel — Design

**Date:** 2026-07-19
**Status:** Approved by operator (design); implementation not started

## Purpose

A second, bar-side physical control point for the basement lights: an ESP32-2432S028R
"Cheap Yellow Display" (classic 2.8", 320×240, resistive touch) mounted **portrait**,
running ESPHome + LVGL. It recreates a focused subset of the basement wall-tablet
dashboard (see `2026-07-19-basement-tablet-dashboard-design.md`) with the same
charcoal/amber visual language.

## Scope

In scope: one ESPHome YAML (`ha/cyd_bar/basement-cyd-bar.yaml`), a sync test tying its
All Off entity list to `ha/basement_tablet/entities.py`, and a README covering flash +
HA setup steps.

Out of scope: dimming for cans/game room/foyer, the cans-by-row slider, multi-page UI,
the CYD's RGB LED and LDR (unused/off), repo-side build tooling (operator builds and
flashes in the HA ESPHome add-on).

## Hardware & platform

- Board: ESP32-2432S028R — no PSRAM, ILI9341 240×320 (native portrait), XPT2046 touch.
- ESPHome native LVGL; partial frame buffer (25%) since there is no PSRAM.
- Pins (standard CYD): display SPI CLK 14 / MOSI 13 / MISO 12 / CS 15 / DC 2;
  touch on its own bus CLK 25 / MOSI 32 / MISO 39 / CS 33 (IRQ 36); backlight GPIO 21
  via LEDC (dimmable).
- Wifi/API/OTA credentials come from the ESPHome add-on's `secrets.yaml` — never in
  this repo.

## UI (240×320 portrait)

Charcoal background, amber = lit, matching the tablet's tokens in spirit.

```
┌───────────┬───────────┐
│    Bar    │   Cans    │   toggle tiles ~112×96, amber when on
├───────────┴───────────┤
│  Bar Dimmer      64%  │
│  [▓▓▓▓▓▓▓▓░░░░░░░░░]  │   horizontal slider, full width, ~48px tall
├───────────────────────┤
│      ALL  OFF  ⏻      │   full-width, red accent, ~52px
└───────────────────────┘
```

- **Bar / Cans tiles**: tap → `homeassistant.toggle` on `light.bar_lights` /
  `light.basement_cans`; checked state driven by imported HA state.
- **Dimmer slider**: shows current `light.bar_lights` brightness as percent (0 when
  off). On release: value > 0 → `light.turn_on` with `brightness_pct`; value 0 →
  `light.turn_off`. HA→device slider updates are suppressed while the slider is
  pressed (no fighting mid-drag).
- **All Off**: `homeassistant.turn_off` targeting the **same entity list as the
  tablet's All Off** (`ALL_OFF_TARGETS`: 5 zones + 11 can-row lights).
- **Status dot**: small red dot top corner while the HA API is disconnected, so a dead
  panel is visibly dead instead of silently eating taps.

## HA integration

- Native ESPHome API. Imported states: `light.bar_lights` (state + `brightness`
  attribute), `light.basement_cans` (state).
- Post-adoption manual step: enable **"Allow the device to make Home Assistant
  actions"** in the ESPHome integration settings for this device — without it every
  tap is silently dropped.

## Idle behavior

- 30 s idle → backlight dims to 30%.
- 5 min idle → LVGL paused + backlight off.
- Waking touch only wakes (backlight on, LVGL resumed); it never fires a widget.

## Sync with the tablet (known tradeoff)

The operator builds this YAML in the ESPHome add-on, so the All Off entity list is
hand-maintained in the YAML rather than generated from `entities.py`. Guard: a pytest
(`tests/test_cyd_bar.py`) parses the YAML and asserts its All Off list equals
`ALL_OFF_TARGETS`. Changing the tablet's zones breaks the test, which says
"re-paste the YAML into the add-on".

## Error handling

- Wifi/API drops: ESPHome auto-reconnects; the status dot shows disconnected state.
- HA restart: imported states re-sync on reconnect; widgets update to match.

## Testing

- `tests/test_cyd_bar.py`: YAML parses; All Off list == `ALL_OFF_TARGETS`; the two
  imported entity ids exist in `entities.py`'s `ZONES`.
- On-device manual verification: both toggles both directions, external change
  (tablet/Hue app) reflects on tiles and slider, slider dim + release-at-0 off,
  All Off, idle dim/off/wake-without-firing, status dot during an HA restart.

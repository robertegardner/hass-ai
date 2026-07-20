# CYD Panel Template — Design

**Date:** 2026-07-20
**Status:** Approved by operator (design); implementation not started

## Purpose

A repeatable workflow for building wall-mounted CYD light controllers: define a panel's
widgets and entities declaratively, generate a paste-ready ESPHome YAML, flash it in the
HA ESPHome add-on. Generalizes the one-off bar panel
(`2026-07-19-cyd-bar-panel-design.md`) into a template covering a handful of future
panels around the house.

## Scope

In scope: `ha/cyd_panels/` package (`panels.py` definitions, `build_panels.py`
generator, generated `<name>.yaml` per panel, README), migration of the deployed bar
panel into the template, `tests/test_cyd_panels.py` replacing `tests/test_cyd_bar.py`,
removal of `ha/cyd_bar/`.

Out of scope: scene/script buttons, landscape orientation, non-2.8" CYD variants,
multi-page UIs, repo-side flashing tooling (the add-on builds and flashes), any HA
writes.

## Hardware assumption

Every panel is a classic ESP32-2432S028R (2.8", ILI9341 240×320, XPT2046 resistive
touch, no PSRAM) mounted **portrait**. One pin map and driver config baked into the
generator — identical to the proven bar panel blocks (display SPI 14/13/12, CS 15,
DC 2; touch bus 25/32/39, CS 33, IRQ 36; backlight LEDC GPIO 21; `buffer_size: 25%`;
`on_touch` backlight wake; esp-idf framework).

## Panel definition schema (`panels.py`)

Ordered widget list per panel; vocabulary is exactly three widget types:

```python
PANELS = [
    Panel(
        name="basement-cyd-bar",          # ESPHome device name and YAML filename
        friendly_name="Basement Bar Panel",
        secret_prefix="cyd_bar",          # -> !secret cyd_bar_api_key/_ota_password/_ap_password
        touch_mirror_x=True,              # per-unit touch quirk (default False)
        widgets=[
            Toggle("BAR", "light.bar_lights"),        # light.* or switch.*, tap = toggle
            Toggle("CANS", "light.basement_cans"),
            Dimmer("Bar Dimmer", "light.bar_lights"),  # release-to-apply, 0 = off
            GroupOff("ALL OFF", ALL_OFF_TARGETS),      # homeassistant.turn_off on a list
        ],
    ),
]
```

- `secret_prefix` defaults to the slugified `name`; the bar panel pins `cyd_bar` to
  match its already-deployed secrets.
- The bar panel's `GroupOff` imports `ALL_OFF_TARGETS` from
  `ha/basement_tablet/entities.py` — sync by Python import, which cannot drift.
- At most one `GroupOff` per panel; it renders pinned to the bottom regardless of its
  position in the list.

## Auto-layout (fixed rules, 240×320)

- Status dot row on top (12 px dot centered, y 4).
- Toggles flow two per row (109 px wide, ~108 px tall, 6 px inner gap); a lone trailing
  toggle spans the full 224 px row width.
- Each `Dimmer` takes a full-width block: label + percent row, then a 48 px slider.
- `GroupOff` is a full-width 52 px button pinned to the bottom (red accent).
- 12 px gaps between blocks, 8 px side margins.
- **Fit checker:** fixed heights, no auto-shrinking. If the widget stack exceeds the
  available height the generator raises with the panel name, needed px, and available
  px. Practical capacity ≈ one toggle row + one dimmer + GroupOff (the bar panel), or
  two toggle rows + GroupOff (up to 4 toggles).

## Behavior (carried verbatim from the bar panel)

Charcoal/amber theme; checked-state amber glow on toggles; dimmer release-to-apply with
`slider_pressed` echo suppression and NaN-when-off handling; status dot gated on
`api.connected` (shows only when no API clients remain); idle 30 s dim to 30%, 5 min
LVGL pause + backlight off, wake on release never fires a widget; CSV strings for
multi-entity service data; `homeassistant.action` for all service calls; Roboto via
gfonts; text labels only (no icon fonts).

## Migration of the bar panel

- Definition moves into `panels.py`; generated output lands at
  `ha/cyd_panels/basement-cyd-bar.yaml`; `ha/cyd_bar/` (YAML + README) is removed.
- The generated YAML must be functionally identical to the deployed config — same
  device name, same secret names, same widgets/behavior — so the flashed panel accepts
  it as a routine OTA update. (Cosmetic YAML formatting may differ; the entity wiring,
  ids, and hardware blocks may not.)
- `tests/test_cyd_bar.py` is deleted in the same commit that lands its replacement
  coverage in `tests/test_cyd_panels.py`.

## Testing (`tests/test_cyd_panels.py`)

- **Freshness:** every generated YAML on disk equals the current `build()` output —
  catches "edited panels.py, forgot to rebuild/re-paste".
- **Entity wiring (per panel, auto-covers future panels):** parsed YAML toggle targets,
  dimmer `light.turn_on`/`light.turn_off` targets, and GroupOff CSV (order included)
  equal the panel definition's.
- **Bar-panel parity:** its GroupOff equals `ALL_OFF_TARGETS` (guards the import wiring
  itself).
- **Layout invariants:** all widgets within 240×320; no overlapping rects; the fit
  checker raises on a deliberately overstuffed panel.

## Workflow per new panel (documented in `ha/cyd_panels/README.md`)

1. Add a `Panel(...)` to `panels.py`; run
   `uv run --group weather python ha/cyd_panels/build_panels.py`.
2. Add `<prefix>_api_key` / `<prefix>_ota_password` / `<prefix>_ap_password` to the
   add-on's `secrets.yaml`.
3. Paste `ha/cyd_panels/<name>.yaml` into the add-on; first flash over USB, then OTA.
4. Adopt in HA and enable "Allow the device to make Home Assistant actions".
5. If this unit's touch is left/right inverted, set `touch_mirror_x=True`, rebuild,
   re-paste. Run the README's on-device verification checklist.

## Error handling

- Generator: fit-checker failures and duplicate panel names / multiple GroupOffs raise
  with actionable messages; builder exits non-zero.
- Runtime behavior is unchanged from the bar panel (auto-reconnect, status dot).

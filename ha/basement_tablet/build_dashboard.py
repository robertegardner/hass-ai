"""Generate the basement tablet dashboard config (dashboard.json).

Design: "switch panel at night" — charcoal glass, six rounded tiles, warm amber
glow on whatever is lit, one tall cans-by-row slider column. Built for an 8.7"
1340x800 landscape wall tablet (fullscreen, HA header visible) replacing the
physical switches: tap = toggle, drag a tile = dim, no chrome.

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
TEXT_HI = "#f2f0ec"
TEXT_LO = "rgba(242,240,236,0.55)"
RADIUS = "22px"

TILE_MOD = f"""
ha-card {{
  height: 100%;
  box-sizing: border-box;
  border-radius: {RADIUS};
  background: {TILE_BG};
  border: 1px solid {TILE_EDGE};
  padding: 14px;
  --icon-size: 40px;
  --card-primary-font-size: 20px;
  --card-primary-font-weight: 600;
  --card-secondary-font-size: 13px;
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
  --card-primary-font-size: 16px;
  --card-secondary-font-size: 12px;
  --primary-text-color: {TEXT_HI};
  --secondary-text-color: {TEXT_LO};
  --icon-size: 32px;
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
        light_tile(ZONES["gameroom"], "Game Room", "mdi:gamepad-variant", "game"),
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
            "grid-template-rows": "repeat(3, minmax(0, 1fr))",
            "grid-template-areas": (
                '"cans bar rows" "game foyer rows" "bath alloff rows"'
            ),
            "grid-gap": "12px",
            "padding": "12px",
            "height": "calc(100vh - var(--header-height, 56px))",
            "mediaquery": {
                # portrait fallback: single column, slider last
                "(orientation: portrait)": {
                    "grid-template-columns": "1fr",
                    "grid-template-rows": "repeat(6, 140px) 1fr",
                    "grid-template-areas": (
                        '"cans" "bar" "game" "foyer" "bath" "alloff" "rows"'
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

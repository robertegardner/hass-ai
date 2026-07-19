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

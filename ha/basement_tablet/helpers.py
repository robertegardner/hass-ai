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
            "name": FOYER_GROUP_TITLE,
            "entities": FOYER_MEMBERS,
            "hide_members": False,
            "all": False,  # group is on if any member is on
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

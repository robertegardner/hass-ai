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

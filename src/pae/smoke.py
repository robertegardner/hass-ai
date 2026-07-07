"""Read-only smoke test against a live Home Assistant instance.

Proves REST auth, WebSocket auth, and event flow. Uses only read endpoints
and the subscribe_events WS command — writes nothing.
"""

import asyncio
from collections import Counter

from pae.config import get_settings
from pae.ha.client import HARestClient, HAWebSocketClient
from pae.ha.models import HAEvent
from pae.logging import get_logger

log = get_logger(__name__)


async def run_smoke(duration: int = 60) -> int:
    settings = get_settings()
    token = settings.ha_token.get_secret_value()
    if not token:
        print("FAIL: HA_TOKEN is not set (export it or put it in .env)")
        return 2

    print(f"HA URL: {settings.ha_url}")

    async with HARestClient(settings.ha_url, token) as rest:
        config = await rest.get_config()
        print(f"REST OK — Home Assistant {config.get('version')} "
              f"({config.get('location_name')})")

        states = await rest.get_states()
        domains = Counter(s.domain for s in states)
        top = ", ".join(f"{d}: {n}" for d, n in domains.most_common(5))
        print(f"Entities: {len(states)}  (top domains — {top})")

    event_count = 0

    def on_event(event: HAEvent) -> None:
        nonlocal event_count
        event_count += 1

    ws = HAWebSocketClient(settings.ha_ws_url, token)
    ws.subscribe_events("state_changed", on_event)
    runner = asyncio.create_task(ws.run())
    try:
        await ws.wait_connected(timeout=15)
        print(f"WS authenticated — listening for state_changed for {duration}s...")
        for elapsed in range(10, duration + 1, 10):
            await asyncio.sleep(10)
            print(f"  {elapsed:>3}s: {event_count} events")
        remainder = duration % 10
        if remainder:
            await asyncio.sleep(remainder)
    except TimeoutError:
        print("FAIL: WebSocket did not authenticate within 15s")
        return 1
    finally:
        await ws.stop()
        runner.cancel()

    rate = event_count / duration * 60
    print(f"Done: {event_count} events in {duration}s ({rate:.1f} events/min)")
    return 0

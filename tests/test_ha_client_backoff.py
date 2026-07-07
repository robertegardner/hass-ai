import asyncio
import random

from conftest import VALID_TOKEN

import pae.ha.client as client_mod
from pae.ha.client import HAWebSocketClient, next_delay


def test_next_delay_bounds():
    rng = random.Random(42)
    for attempt in range(12):
        delay = next_delay(attempt, base=1.0, cap=60.0, rng=rng)
        assert 0 <= delay <= min(60.0, 2**attempt)


def test_next_delay_caps_at_ceiling():
    rng = random.Random(7)
    delays = [next_delay(50, base=1.0, cap=60.0, rng=rng) for _ in range(100)]
    assert all(d <= 60.0 for d in delays)


async def test_reconnect_reauths_and_resubscribes(fake_ha, monkeypatch):
    monkeypatch.setattr(client_mod, "next_delay", lambda *a, **kw: 0.01)
    fake_ha.drop_connections = 1  # kill the first connection right after subscribe

    client = HAWebSocketClient(fake_ha.url, VALID_TOKEN)
    client.subscribe_events("state_changed", lambda e: None)

    runner = asyncio.create_task(client.run())
    try:
        # wait until the second connection has authenticated
        for _ in range(100):
            if fake_ha.connections >= 2 and client.connected:
                break
            await asyncio.sleep(0.05)
    finally:
        await client.stop()
        runner.cancel()

    assert fake_ha.connections == 2
    auth_frames = [f for f in fake_ha.received_frames if f.get("type") == "auth"]
    sub_frames = [f for f in fake_ha.received_frames if f.get("type") == "subscribe_events"]
    assert len(auth_frames) == 2
    assert len(sub_frames) == 2

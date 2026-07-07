import asyncio

import pytest
from conftest import VALID_TOKEN

from pae.ha.client import HAWebSocketClient
from pae.ha.errors import HAAuthError


async def test_auth_handshake_and_event_dispatch(fake_ha):
    fake_ha.events_on_subscribe = [
        {"event_type": "state_changed", "data": {"entity_id": "light.kitchen"}}
    ]
    received = []
    client = HAWebSocketClient(fake_ha.url, VALID_TOKEN)
    client.subscribe_events("state_changed", lambda e: received.append(e))

    runner = asyncio.create_task(client.run())
    try:
        await client.wait_connected(timeout=5)
        await asyncio.sleep(0.2)
    finally:
        await client.stop()
        runner.cancel()

    auth_frame = fake_ha.received_frames[0]
    assert auth_frame == {"type": "auth", "access_token": VALID_TOKEN}
    sub_frame = fake_ha.received_frames[1]
    assert sub_frame["type"] == "subscribe_events"
    assert sub_frame["event_type"] == "state_changed"
    assert len(received) == 1
    assert received[0].event_type == "state_changed"
    assert received[0].data["entity_id"] == "light.kitchen"


async def test_auth_invalid_is_fatal(fake_ha):
    client = HAWebSocketClient(fake_ha.url, "wrong-token")
    with pytest.raises(HAAuthError):
        await client.run()
    # fatal: no reconnect attempted
    assert fake_ha.connections == 1

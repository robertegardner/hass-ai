import pytest

from pae.ha.client import ALLOWED_OUTBOUND_TYPES, HARestClient, HAWebSocketClient
from pae.ha.errors import ReadOnlyViolation


def test_call_service_blocked_in_read_only_mode():
    client = HARestClient("http://example.invalid:8123", "token", read_only=True)
    with pytest.raises(ReadOnlyViolation):
        client.call_service("light", "turn_on", entity_id="light.kitchen")


def test_call_service_not_implemented_even_when_writable():
    # Phase 0: even with read_only off, there is no write implementation
    client = HARestClient("http://example.invalid:8123", "token", read_only=False)
    with pytest.raises(NotImplementedError):
        client.call_service("light", "turn_on")


async def test_ws_outbound_whitelist_blocks_unknown_types():
    client = HAWebSocketClient("ws://example.invalid/api/websocket", "token")
    with pytest.raises(ReadOnlyViolation):
        await client._send({"type": "call_service", "domain": "light", "service": "turn_on"})


def test_whitelist_is_read_only_frame_types():
    assert ALLOWED_OUTBOUND_TYPES == {
        "auth",
        "subscribe_events",
        "ping",
        "get_states",
        "config/entity_registry/list",
        "config/area_registry/list",
        "config/device_registry/list",
    }
    # nothing write-shaped may ever appear here without a phase review
    for forbidden in ("call_service", "config/automation/create", "config/label_registry/create"):
        assert forbidden not in ALLOWED_OUTBOUND_TYPES

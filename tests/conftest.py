import json

import pytest
from aiohttp import web

VALID_TOKEN = "test-token"
HA_VERSION = "2026.7.0"


class FakeHA:
    """Minimal Home Assistant WebSocket server: auth handshake, subscriptions,
    scripted event delivery, and optional connection drops for reconnect tests."""

    def __init__(self) -> None:
        self.connections = 0
        self.received_frames: list[dict] = []
        self.drop_connections = 0  # close this many connections right after subscribe
        self.events_on_subscribe: list[dict] = []  # event payloads sent after subscribe
        self.url = ""

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        await ws.send_json({"type": "auth_required", "ha_version": HA_VERSION})
        auth = await ws.receive_json()
        self.received_frames.append(auth)
        if auth.get("access_token") != VALID_TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": HA_VERSION})
        async for msg in ws:
            frame = json.loads(msg.data)
            self.received_frames.append(frame)
            if frame.get("type") == "subscribe_events":
                await ws.send_json(
                    {"id": frame["id"], "type": "result", "success": True, "result": None}
                )
                if self.drop_connections > 0:
                    self.drop_connections -= 1
                    await ws.close()
                    return ws
                for event in self.events_on_subscribe:
                    await ws.send_json({"id": frame["id"], "type": "event", "event": event})
        return ws


@pytest.fixture
async def fake_ha():
    server = FakeHA()
    app = web.Application()
    app.router.add_get("/api/websocket", server._handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    server.url = f"ws://127.0.0.1:{port}/api/websocket"
    yield server
    await runner.cleanup()

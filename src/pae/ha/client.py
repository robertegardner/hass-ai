import asyncio
import functools
import itertools
import json
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from pae.ha.errors import HAAuthError, HAConnectionError, ReadOnlyViolation
from pae.ha.models import EntityState, HAEvent
from pae.logging import get_logger
from pae.metrics import HA_EVENTS_RECEIVED, HA_WS_CONNECTED, HA_WS_RECONNECTS

log = get_logger(__name__)

# Only these message types may ever leave the WebSocket client in Phase 0.
# Widening this set is a deliberate, reviewed act in a later phase.
ALLOWED_OUTBOUND_TYPES = frozenset({"auth", "subscribe_events", "ping"})

EventCallback = Callable[[HAEvent], Awaitable[None] | None]


def next_delay(
    attempt: int, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None
) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base * 2**attempt))."""
    upper = min(cap, base * (2**attempt))
    return (rng or random).uniform(0, upper)


def writes_to_ha(fn):
    """Guard for any method that would mutate Home Assistant state.

    Raises ReadOnlyViolation before the wrapped method body runs when the
    client is in read-only mode. Every future write method must carry this.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if self.read_only:
            raise ReadOnlyViolation(
                f"{fn.__name__} blocked: client is in read-only mode"
            )
        return fn(self, *args, **kwargs)

    return wrapper


class HARestClient:
    """Thin wrapper over the Home Assistant REST API.

    Phase 0 exposes read endpoints only. Write-shaped methods (call_service,
    automation config, label registry) arrive in later phases and must be
    decorated with @writes_to_ha.
    """

    def __init__(self, base_url: str, token: str, *, read_only: bool = True) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self.read_only = read_only
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HARestClient":
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get(self, path: str) -> Any:
        assert self._session is not None, "use 'async with HARestClient(...)'"
        try:
            async with self._session.get(f"{self._base_url}{path}") as resp:
                if resp.status == 401:
                    raise HAAuthError("REST API rejected the token (401)")
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            raise HAConnectionError(f"GET {path} failed: {e}") from e

    async def ping(self) -> bool:
        data = await self._get("/api/")
        return data.get("message") == "API running."

    async def get_config(self) -> dict[str, Any]:
        return await self._get("/api/config")

    async def get_states(self) -> list[EntityState]:
        data = await self._get("/api/states")
        return [EntityState.model_validate(item) for item in data]

    @writes_to_ha
    def call_service(self, domain: str, service: str, **data: Any):
        raise NotImplementedError("Service calls arrive in a later phase")


class HAWebSocketClient:
    """Home Assistant WebSocket client: auth, event subscription, reconnect.

    Usage: register callbacks with subscribe_events(), then await run().
    run() supervises the connection forever — authenticating, re-subscribing
    after every reconnect, and backing off with full jitter between attempts.
    Auth rejection is fatal and never retried.
    """

    def __init__(self, ws_url: str, token: str, *, read_only: bool = True) -> None:
        self._ws_url = ws_url
        self._token = token
        self.read_only = read_only
        self._id_counter = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: list[tuple[str, EventCallback]] = []
        self._active_subs: dict[int, EventCallback] = {}
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected_event = asyncio.Event()
        self._stopping = False

    @property
    def connected(self) -> bool:
        return self._connected_event.is_set()

    async def wait_connected(self, timeout: float | None = None) -> None:  # noqa: ASYNC109
        async with asyncio.timeout(timeout):
            await self._connected_event.wait()

    def subscribe_events(self, event_type: str, callback: EventCallback) -> None:
        """Register a callback for an event type. Takes effect on (re)connect."""
        self._subscriptions.append((event_type, callback))

    async def _send(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type not in ALLOWED_OUTBOUND_TYPES:
            raise ReadOnlyViolation(
                f"outbound message type {msg_type!r} is not in the Phase 0 whitelist"
            )
        assert self._ws is not None
        await self._ws.send_str(json.dumps(message))

    async def _send_command(self, message: dict[str, Any]) -> Any:
        """Send a command frame with a fresh id and await its result frame."""
        msg_id = next(self._id_counter)
        message["id"] = msg_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send(message)
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(msg_id, None)

    async def _authenticate(self, session: aiohttp.ClientSession) -> None:
        self._ws = await session.ws_connect(self._ws_url, heartbeat=30)
        # HA speaks first: read auth_required, then send our token.
        first = await self._ws.receive_json()
        if first.get("type") != "auth_required":
            raise HAConnectionError(f"expected auth_required, got {first.get('type')!r}")
        await self._send({"type": "auth", "access_token": self._token})
        reply = await self._ws.receive_json()
        if reply.get("type") == "auth_invalid":
            raise HAAuthError(f"WebSocket auth rejected: {reply.get('message')}")
        if reply.get("type") != "auth_ok":
            raise HAConnectionError(f"unexpected auth reply: {reply.get('type')!r}")
        log.info("ha_ws_authenticated", ha_version=reply.get("ha_version"))

    async def _resubscribe(self) -> None:
        self._active_subs.clear()
        for event_type, callback in self._subscriptions:
            msg_id = next(self._id_counter)
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = future
            self._active_subs[msg_id] = callback
            await self._send(
                {"type": "subscribe_events", "event_type": event_type, "id": msg_id}
            )
        # results are confirmed by the listen loop; no need to block here

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        frame_type = frame.get("type")
        if frame_type == "result":
            future = self._pending.get(frame.get("id", -1))
            if future and not future.done():
                if frame.get("success"):
                    future.set_result(frame.get("result"))
                else:
                    future.set_exception(
                        HAConnectionError(f"command failed: {frame.get('error')}")
                    )
        elif frame_type == "event":
            callback = self._active_subs.get(frame.get("id", -1))
            event = HAEvent.model_validate(frame.get("event", {}))
            HA_EVENTS_RECEIVED.labels(event_type=event.event_type or "unknown").inc()
            if callback:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result

    async def _listen(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._dispatch(json.loads(msg.data))
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break
        raise HAConnectionError("WebSocket connection closed")

    async def run(self) -> None:
        """Supervise the connection until stop() is called. HAAuthError is fatal."""
        attempt = 0
        loop = asyncio.get_running_loop()
        async with aiohttp.ClientSession() as session:
            while not self._stopping:
                connected_at: float | None = None
                try:
                    await self._authenticate(session)
                    connected_at = loop.time()
                    self._connected_event.set()
                    HA_WS_CONNECTED.set(1)
                    await self._resubscribe()
                    await self._listen()
                except HAAuthError:
                    raise
                except (aiohttp.ClientError, HAConnectionError, OSError) as e:
                    if self._stopping:
                        break
                    log.warning("ha_ws_disconnected", error=str(e))
                finally:
                    self._connected_event.clear()
                    HA_WS_CONNECTED.set(0)
                    if self._ws is not None and not self._ws.closed:
                        await self._ws.close()
                if self._stopping:
                    break
                # a connection that survived >=30s earns a fresh backoff ladder
                if connected_at is not None and loop.time() - connected_at >= 30:
                    attempt = 0
                delay = next_delay(attempt)
                attempt += 1
                HA_WS_RECONNECTS.inc()
                log.info("ha_ws_reconnecting", attempt=attempt, delay=round(delay, 2))
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

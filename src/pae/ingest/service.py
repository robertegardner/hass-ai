"""The ingester: an independent WebSocket listener that never touches HA's
own recorder database. Subscribes to state_changed (behavioral events),
automation_triggered/script_started (for attribution), and maintains
presence snapshots, per-minute context frames, and the entity registry
mirror."""

import asyncio
import fnmatch
from datetime import UTC, datetime
from typing import Any

from prometheus_client import start_http_server

from pae.config import Settings, get_settings
from pae.db.engine import create_engine
from pae.db.models import ContextFrame, Event, PresenceSnapshot
from pae.ha.client import HARestClient, HAWebSocketClient
from pae.ha.models import HAEvent
from pae.ingest.attribution import AUTOMATION, PAE, ContextCache, attribute
from pae.ingest.context import NullTravelCalendar, day_type, season
from pae.ingest.filters import PERSON_DETECTED_PATTERN, parse_denylist, should_ingest
from pae.ingest.presence import person_object_id, room_from_camera_name
from pae.ingest.registry import build_registry_rows, upsert_registry
from pae.ingest.writer import BatchWriter
from pae.logging import get_logger
from pae.metrics import INGEST_EVENTS_ATTRIBUTED, INGEST_EVENTS_FILTERED

log = get_logger(__name__)


def _parse_time(state: dict[str, Any] | None, fallback: datetime | None) -> datetime:
    raw = (state or {}).get("last_updated")
    if raw:
        return datetime.fromisoformat(raw)
    return fallback or datetime.now(UTC)


class IngestService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine = create_engine()
        self._writer = BatchWriter(self._engine, flush_size=settings.ingest_flush_size)
        self._cache = ContextCache()
        self._denylist = parse_denylist(settings.ingest_denylist)
        self._persons_exclude = {
            p.strip() for p in settings.persons_exclude.split(",") if p.strip()
        }
        # entity_ids of PAE-created automations — populated in Phase 5+
        self._pae_automations: set[str] = set()
        self._travel = NullTravelCalendar()
        self._person_entities: list[str] = []
        token = settings.ha_token.get_secret_value()
        self._ws = HAWebSocketClient(settings.ha_ws_url, token)
        self._rest = HARestClient(settings.ha_url, token)

    # -- event handlers -----------------------------------------------------

    async def on_state_changed(self, event: HAEvent) -> None:
        data = event.data
        entity_id = data.get("entity_id", "")
        new = data.get("new_state")
        old = data.get("old_state")
        if not entity_id or new is None or old is None:
            return  # entity appeared/disappeared — not a behavioral action
        if new.get("state") == old.get("state"):
            return  # attribute-only change; deliberately dropped for noise control
        if new.get("state") in ("unavailable", "unknown") or old.get("state") in (
            "unavailable",
            "unknown",
        ):
            return  # device (dis)connect, not behavior
        attrs = new.get("attributes") or {}
        if not should_ingest(entity_id, attrs.get("device_class"), self._denylist):
            INGEST_EVENTS_FILTERED.inc()
            return

        ctx = event.context or {}
        triggered_by = attribute(
            ctx.get("id"), ctx.get("parent_id"), ctx.get("user_id"), self._cache
        )
        INGEST_EVENTS_ATTRIBUTED.labels(triggered_by=triggered_by).inc()
        when = _parse_time(new, event.time_fired)

        self._writer.add(
            Event.__table__,
            {
                "time": when,
                "entity_id": entity_id,
                "domain": entity_id.split(".", 1)[0],
                "old_state": old.get("state"),
                "new_state": new.get("state"),
                "attrs": attrs,
                "context_id": ctx.get("id"),
                "context_parent_id": ctx.get("parent_id"),
                "user_id": ctx.get("user_id"),
                "triggered_by": triggered_by,
            },
        )

        self._maybe_presence(entity_id, old, new, attrs, when)

        if self._writer.needs_flush():
            await self._writer.flush()

    def _maybe_presence(
        self,
        entity_id: str,
        old: dict[str, Any],
        new: dict[str, Any],
        attrs: dict[str, Any],
        when: datetime,
    ) -> None:
        if entity_id.startswith("person.") and entity_id not in self._persons_exclude:
            self._writer.add(
                PresenceSnapshot.__table__,
                {
                    "time": when,
                    "person": person_object_id(entity_id),
                    "room": new.get("state"),
                    "source": "person",
                },
            )
        elif (
            fnmatch.fnmatch(entity_id, PERSON_DETECTED_PATTERN)
            and old.get("state") == "off"
            and new.get("state") == "on"
        ):
            friendly = attrs.get("friendly_name") or entity_id
            self._writer.add(
                PresenceSnapshot.__table__,
                {
                    "time": when,
                    "person": "unknown",
                    "room": room_from_camera_name(friendly),
                    "source": "unifi",
                },
            )

    async def on_automation_activity(self, event: HAEvent) -> None:
        """automation_triggered / script_started feed the attribution cache."""
        ctx_id = (event.context or {}).get("id")
        if not ctx_id:
            return
        entity_id = event.data.get("entity_id", "")
        source = PAE if entity_id in self._pae_automations else AUTOMATION
        self._cache.add(ctx_id, source)

    # -- periodic loops -----------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.ingest_flush_interval)
            await self._writer.flush()

    async def _context_frame_loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.context_frame_interval)
            try:
                await self._write_context_frame()
            except Exception as e:
                log.warning("context_frame_failed", error=str(e))

    async def _write_context_frame(self) -> None:
        persons_home: dict[str, str] = {}
        for entity_id in self._person_entities:
            state = await self._rest.get_state(entity_id)
            if state:
                persons_home[person_object_id(entity_id)] = state.state
        sun = await self._rest.get_state("sun.sun")
        temp_state = await self._rest.get_state(self._settings.outside_temp_entity)
        outside_temp: float | None = None
        if temp_state and temp_state.state not in ("unavailable", "unknown"):
            try:
                outside_temp = float(temp_state.state)
            except ValueError:
                pass
        now = datetime.now(UTC)
        today = now.astimezone().date()  # day_type/season in local time
        self._writer.add(
            ContextFrame.__table__,
            {
                "time": now,
                "persons_home": persons_home,
                "sun_state": sun.state if sun else None,
                "day_type": day_type(today, self._travel),
                "season": season(today),
                "outside_temp": outside_temp,
            },
        )

    async def _registry_loop(self) -> None:
        while True:
            try:
                await self._ws.wait_connected(timeout=60)
                entities = await self._ws.get_entity_registry()
                devices = await self._ws.get_device_registry()
                areas = await self._ws.get_area_registry()
                rows = build_registry_rows(entities, devices, areas)
                await upsert_registry(self._engine, rows)
            except Exception as e:
                log.warning("registry_refresh_failed", error=str(e))
                await asyncio.sleep(300)
                continue
            await asyncio.sleep(self._settings.registry_refresh_hours * 3600)

    # -- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        start_http_server(self._settings.worker_metrics_port)
        async with self._rest:
            states = await self._rest.get_states()
            self._person_entities = sorted(
                s.entity_id
                for s in states
                if s.domain == "person" and s.entity_id not in self._persons_exclude
            )
            log.info("ingest_starting", persons=self._person_entities)

            self._ws.subscribe_events("state_changed", self.on_state_changed)
            self._ws.subscribe_events("automation_triggered", self.on_automation_activity)
            self._ws.subscribe_events("script_started", self.on_automation_activity)

            tasks = [
                asyncio.create_task(self._ws.run(), name="ws"),
                asyncio.create_task(self._flush_loop(), name="flush"),
                asyncio.create_task(self._context_frame_loop(), name="context_frames"),
                asyncio.create_task(self._registry_loop(), name="registry"),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()  # re-raise whatever killed us
            finally:
                for task in tasks:
                    task.cancel()
                await self._writer.flush()
                await self._engine.dispose()


def run_ingest() -> None:
    from pae.db.migrate import run_migrations

    run_migrations()
    asyncio.run(IngestService(get_settings()).run())

"""Entity registry mirror: friendly names, areas, device classes.

Area resolution follows HA's own rule: an entity's area is its own area_id
if set, else its device's area_id.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from pae.db.models import EntityRegistryEntry
from pae.logging import get_logger

log = get_logger(__name__)


def build_registry_rows(
    entities: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    areas: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    area_names = {a["area_id"]: a.get("name") for a in areas}
    device_areas = {d["id"]: d.get("area_id") for d in devices}

    rows = []
    for e in entities:
        entity_id = e["entity_id"]
        area_id = e.get("area_id") or device_areas.get(e.get("device_id") or "")
        rows.append(
            {
                "entity_id": entity_id,
                "domain": entity_id.split(".", 1)[0],
                "friendly_name": e.get("name") or e.get("original_name"),
                "area_id": area_id,
                "area_name": area_names.get(area_id) if area_id else None,
                "device_id": e.get("device_id"),
                "device_class": e.get("device_class") or e.get("original_device_class"),
                "updated_at": now,
            }
        )
    return rows


# 8 columns per row; keep well under Postgres's 65535 bind-parameter limit
_CHUNK = 4000


async def upsert_registry(engine: AsyncEngine, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    table = EntityRegistryEntry.__table__
    async with engine.begin() as conn:
        for start in range(0, len(rows), _CHUNK):
            chunk = rows[start : start + _CHUNK]
            stmt = pg_insert(table).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[table.c.entity_id],
                set_={
                    c.name: stmt.excluded[c.name] for c in table.columns if c.name != "entity_id"
                },
            )
            await conn.execute(stmt)
    log.info("entity_registry_refreshed", entities=len(rows))

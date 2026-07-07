import asyncio
from typing import Any

from sqlalchemy import Table, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from pae.logging import get_logger
from pae.metrics import INGEST_FLUSH_ERRORS, INGEST_ROWS_WRITTEN

log = get_logger(__name__)


class BatchWriter:
    """Buffers rows per table and flushes on size or interval.

    On flush failure rows stay buffered and are retried on the next flush,
    so a brief database outage loses nothing (bounded by buffer memory).
    """

    def __init__(self, engine: AsyncEngine, flush_size: int = 200) -> None:
        self._engine = engine
        self._flush_size = flush_size
        self._buffers: dict[Table, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    def add(self, table: Table, row: dict[str, Any]) -> None:
        self._buffers.setdefault(table, []).append(row)

    @property
    def pending(self) -> int:
        return sum(len(rows) for rows in self._buffers.values())

    def needs_flush(self) -> bool:
        return self.pending >= self._flush_size

    async def flush(self) -> None:
        async with self._lock:
            for table, rows in self._buffers.items():
                if not rows:
                    continue
                try:
                    async with self._engine.begin() as conn:
                        await conn.execute(insert(table), rows)
                except Exception as e:
                    INGEST_FLUSH_ERRORS.inc()
                    log.warning("flush_failed", table=table.name, rows=len(rows), error=str(e))
                    continue
                INGEST_ROWS_WRITTEN.labels(table=table.name).inc(len(rows))
                self._buffers[table] = []

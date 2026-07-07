from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EntityState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_id: str
    state: str
    attributes: dict[str, Any] = {}
    last_changed: datetime | None = None
    last_updated: datetime | None = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


class HAEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str
    data: dict[str, Any] = {}
    origin: str | None = None
    time_fired: datetime | None = None
    context: dict[str, Any] = {}

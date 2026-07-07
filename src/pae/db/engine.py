from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from pae.config import get_settings


def create_engine() -> AsyncEngine:
    return create_async_engine(get_settings().db_url, pool_pre_ping=True)

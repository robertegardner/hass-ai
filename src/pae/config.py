from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, sourced from environment variables / .env.

    ``pae_read_only`` defaults to True: no code path may write to Home
    Assistant unless it is explicitly disabled, and Phase 0 has no write
    paths at all.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ha_url: str = "http://homeassistant.iot:8123"
    ha_token: SecretStr = SecretStr("")

    ollama_primary: str = "http://192.168.85.61:11434"
    ollama_fallback: str = "http://192.168.6.164:11434"

    db_url: str = "postgresql+psycopg://pae:pae@db:5432/pae"
    redis_url: str = "redis://redis:6379/0"

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    pae_read_only: bool = True

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    worker_metrics_port: int = 9100

    @property
    def ha_ws_url(self) -> str:
        base = self.ha_url.rstrip("/")
        scheme = "wss" if base.startswith("https") else "ws"
        return f"{scheme}://{base.split('://', 1)[1]}/api/websocket"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pathlib import Path

from alembic import command
from alembic.config import Config

import pae.db


def run_migrations() -> None:
    """Upgrade the database to head. Safe to run on every service start."""
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(pae.db.__file__).parent / "migrations")
    )
    command.upgrade(cfg, "head")

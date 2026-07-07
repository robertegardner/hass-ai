import argparse
import asyncio
import sys

from pae import __version__
from pae.config import get_settings
from pae.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pae", description="Predictive Automation Engine")
    parser.add_argument("--version", action="version", version=f"pae {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("api", help="run the FastAPI service")
    sub.add_parser("worker", help="run the RQ worker")
    sub.add_parser("ingest", help="run the HA event ingester")
    sub.add_parser("migrate", help="upgrade the database schema to head")
    smoke = sub.add_parser("smoke", help="read-only smoke test against live Home Assistant")
    smoke.add_argument(
        "--duration", type=int, default=60, help="seconds to listen for events (default 60)"
    )

    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "smoke":
        # human-facing command: console logs regardless of LOG_FORMAT
        configure_logging(settings.log_level, "console")
        from pae.smoke import run_smoke

        return asyncio.run(run_smoke(duration=args.duration))

    configure_logging(settings.log_level, settings.log_format)

    if args.command == "api":
        import uvicorn

        uvicorn.run(
            "pae.api.app:create_app",
            factory=True,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,
        )
        return 0

    if args.command == "worker":
        from pae.worker.main import run_worker

        run_worker()
        return 0

    if args.command == "ingest":
        from pae.ingest.service import run_ingest

        run_ingest()
        return 0

    if args.command == "migrate":
        from pae.db.migrate import run_migrations

        run_migrations()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

import pae.metrics  # noqa: F401 — registers pae_* metrics in the default registry
from pae import __version__
from pae.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PAE", version=__version__, docs_url=None, redoc_url=None)

    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict:
        # Phase 0: no dependencies to probe yet. Redis/DB checks arrive with
        # the first real consumers in Phase 1.
        return {"status": "ready", "read_only": settings.pae_read_only}

    return app

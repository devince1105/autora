from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import autora
from autora.app import load_event_catalogs
from autora.db.session import dispose_engine, get_engine
from autora.infra.settings import get_settings
from autora_api import problems
from autora_api.routers import companies, events


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing/inconsistent configuration
    get_engine()
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    load_event_catalogs()  # stored events of every layer must be parseable
    app = FastAPI(title="Autora API", version=autora.__version__, lifespan=lifespan)
    problems.install(app)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": autora.__version__}

    app.include_router(companies.router)
    app.include_router(events.router)
    return app

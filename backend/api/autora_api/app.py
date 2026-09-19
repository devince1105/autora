from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import autora
from autora.app import load_event_catalogs
from autora.db.session import dispose_engine, get_engine, get_sessionmaker
from autora.infra.settings import get_settings
from autora.realtime.gateway import EventHub
from autora_api import problems
from autora_api.routers import (
    approvals,
    companies,
    events,
    public,
    realtime,
    reporting,
    runs,
    tasks,
    workflows,
    ws,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing/inconsistent configuration
    hub = EventHub(engine=get_engine(), session_factory=get_sessionmaker())
    app.state.hub = hub
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    load_event_catalogs()  # stored events of every layer must be parseable
    app = FastAPI(title="Autora API", version=autora.__version__, lifespan=lifespan)
    problems.install(app)
    # The web app runs on its own origin (localhost:3000 in dev). WebSockets are not subject
    # to CORS; they authenticate with the token in the query string.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": autora.__version__}

    app.include_router(companies.router)
    app.include_router(events.router)
    app.include_router(runs.router)
    app.include_router(tasks.router)
    app.include_router(approvals.router)
    app.include_router(workflows.router)
    app.include_router(realtime.router)
    app.include_router(reporting.router)
    app.include_router(public.router)
    app.include_router(ws.router)
    return app

"""Explicit application factory. Importing this module reads no environment or database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from proofline.api.errors import register_error_handlers
from proofline.api.middleware import register_request_middleware
from proofline.api.routes import register_routes
from proofline.application.service import WorkflowService
from proofline.config import Settings
from proofline.persistence.database import Database, open_database
from proofline.providers.replay import ReplayCatalog, ReplayProvider


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    provider: ReplayProvider | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    owns_database = database is None
    active_database = database if database is not None else open_database(config.database_url)
    active_provider = provider if provider is not None else ReplayCatalog(config.fixtures_dir)
    service = WorkflowService(active_database, config, active_provider)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            # If storage is unavailable now, the readiness gate retries recovery later.
            await run_in_threadpool(service.is_ready)
            yield
        finally:
            if owns_database:
                await run_in_threadpool(active_database.dispose)

    app = FastAPI(title="Proofline replay API", version="0.2.0", lifespan=lifespan)
    app.state.service = service
    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.app_origin, *config.dev_origins],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    register_request_middleware(app, config)
    register_routes(app, service)
    return app

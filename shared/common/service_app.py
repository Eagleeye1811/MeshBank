"""FastAPI application scaffolding shared by all services.

Provides logging, request IDs, error handlers, schema initialisation and the
``/health`` (liveness) and ``/ready`` (readiness) endpoints.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import DeclarativeBase

from common.configuration import BaseServiceSettings
from common.database import Database, redact_database_url
from common.errors import install_error_handlers
from common.logging_setup import RequestContextMiddleware, configure_logging
from common.migrations import prepare_database

logger = logging.getLogger("meshbank.app")


def create_service_app(
    *,
    settings: BaseServiceSettings,
    base: type[DeclarativeBase],
    service_dir: Path,
    title: str,
    description: str,
    on_startup: Callable[[FastAPI], None] | None = None,
    on_shutdown: Callable[[FastAPI], None] | None = None,
    configure_logs: bool = True,
) -> FastAPI:
    if configure_logs:
        configure_logging(settings.service_name, settings.log_level)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "service starting",
            extra={
                "app_env": settings.app_env.value,
                "database": redact_database_url(settings.database_url),
                "db_migration_mode": settings.db_migration_mode,
            },
        )
        if settings.uses_development_secrets():
            logger.warning("using built-in development secrets; never use them in production")
        if on_startup:
            on_startup(app)
        try:
            yield
        finally:
            if on_shutdown:
                on_shutdown(app)
            db.dispose()
            logger.info("service stopped")

    app = FastAPI(title=title, description=description, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    # Done eagerly (not in the lifespan) so a schema problem fails fast and so
    # tests and CLI tools can use the database without running the lifespan.
    # Migrations only move forward; nothing is ever dropped.
    prepare_database(
        mode=settings.db_migration_mode,
        service_dir=service_dir,
        database_url=settings.database_url,
        base=base,
        db=db,
    )
    # Customers authenticate with bearer tokens, not cookies, so credentials are
    # not needed. The mesh headers are added by the sidecar/router; exposing them
    # here keeps CORS headers single-sourced (the proxies add none of their own).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Service-Version",
            "X-Mesh-Track",
            "X-Mesh-Router",
            "Idempotent-Replayed",
        ],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def release_version_header(request, call_next):
        response = await call_next(request)
        response.headers["X-Service-Version"] = settings.service_version
        return response

    install_error_handlers(app)

    @app.get("/health", tags=["operations"], summary="Liveness: the process is running")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": settings.service_version,
        }

    @app.get("/ready", tags=["operations"], summary="Readiness: dependencies owned by this service")
    def ready() -> JSONResponse:
        database_ok = db.ping()
        body = {
            "status": "ready" if database_ok else "not_ready",
            "service": settings.service_name,
            "checks": {"database": "ok" if database_ok else "unavailable"},
        }
        return JSONResponse(status_code=200 if database_ok else 503, content=body)

    return app

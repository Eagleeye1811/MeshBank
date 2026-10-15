"""Account Service: accounts, balances and idempotent balance operations.

Run with ``uvicorn --factory app.main:create_app``.
"""

from pathlib import Path

from fastapi import FastAPI

from app.config import Settings
from app.models import Base
from app.routes import dev, internal, public
from common.configuration import AppEnv
from common.service_app import create_service_app

SERVICE_DIR = Path(__file__).resolve().parents[1]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Account Service",
        description="Customer accounts, balances and internal idempotent debit/credit operations.",
    )
    app.include_router(public)
    app.include_router(internal)
    if settings.dev_seed_enabled and settings.app_env != AppEnv.PRODUCTION:
        app.include_router(dev)
    return app

"""Authentication Service: registration, login and token issuance.

Run with ``uvicorn --factory app.main:create_app``.
"""

from pathlib import Path

from fastapi import FastAPI

from app.config import Settings
from app.models import Base
from app.passwords import PasswordService
from app.routes import jwks_router, router
from common.database import utcnow
from common.service_app import create_service_app

SERVICE_DIR = Path(__file__).resolve().parents[1]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Authentication Service",
        description="User registration, login, identity verification and JWT issuance.",
    )
    app.state.passwords = PasswordService(
        time_cost=settings.argon2_time_cost,
        memory_cost_kib=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
    )
    app.state.clock = utcnow
    app.include_router(router)
    app.include_router(jwks_router)
    return app

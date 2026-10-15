"""Transaction Service: transfers, transaction history and the transfer saga.

Run with ``uvicorn --factory app.main:create_app``.
"""

from pathlib import Path

from fastapi import FastAPI

from app.clients import HttpAccountClient, HttpFraudClient, HttpNotificationClient
from app.config import Settings
from app.models import Base
from app.recovery import RecoveryLoop
from app.routes import internal, public
from common.http_client import InternalClient
from common.service_app import create_service_app

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _client(settings: Settings, target: str, url: str) -> InternalClient:
    return InternalClient(
        target=target,
        base_url=url,
        caller=settings.service_name,
        internal_token=settings.internal_token_value(),
        timeout_seconds=settings.http_timeout_seconds,
        max_attempts=settings.http_max_attempts,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    http_clients = [
        _client(settings, "account-service", settings.account_service_url),
        _client(settings, "fraud-service", settings.fraud_service_url),
        _client(settings, "notification-service", settings.notification_service_url),
    ]

    def on_startup(app: FastAPI) -> None:
        if settings.recovery_interval_seconds > 0:
            app.state.recovery_loop = RecoveryLoop(app, settings.recovery_interval_seconds)
            app.state.recovery_loop.start()

    def on_shutdown(app: FastAPI) -> None:
        loop = getattr(app.state, "recovery_loop", None)
        if loop:
            loop.stop()
        for client in http_clients:
            client.close()

    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Transaction Service",
        description="Transfers, transaction history and a durable, idempotent transfer workflow.",
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    app.state.account_client = HttpAccountClient(http_clients[0])
    app.state.fraud_client = HttpFraudClient(http_clients[1])
    app.state.notification_client = HttpNotificationClient(http_clients[2])
    app.include_router(public)
    app.include_router(internal)
    return app

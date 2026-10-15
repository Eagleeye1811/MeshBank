import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common.security import INTERNAL_TOKEN_HEADER, create_access_token


def make_settings(tmp_path, database_url: str, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": database_url,
        "notification_delivery_log": str(tmp_path / "deliveries.log"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def db_url(database_url_factory) -> str:
    return database_url_factory("notifications")


@pytest.fixture
def settings(tmp_path, db_url) -> Settings:
    return make_settings(tmp_path, db_url)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def internal_headers(settings):
    return {INTERNAL_TOKEN_HEADER: settings.internal_token_value()}


@pytest.fixture
def auth_headers(settings, jwt_keys):
    def _headers(customer_id: str) -> dict[str, str]:
        token, _ = create_access_token(
            settings,
            signing_key=jwt_keys.signing_key,
            subject=customer_id,
            email="c@example.com",
            ttl_seconds=600,
        )
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture
def customer():
    return str(uuid.uuid4())


@pytest.fixture
def payload(customer):
    def _payload(**overrides):
        body = {
            "event_key": f"transaction:{uuid.uuid4()}:COMPLETED",
            "customer_id": customer,
            "type": "TRANSFER_SUCCEEDED",
            "message": "Your transfer of INR 100.00 completed.",
        }
        body.update(overrides)
        return body

    return _payload

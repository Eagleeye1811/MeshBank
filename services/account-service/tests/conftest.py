import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common.security import INTERNAL_TOKEN_HEADER, create_access_token


@pytest.fixture
def settings(database_url_factory) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url_factory("accounts"),
        dev_seed_enabled=True,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(settings, jwt_keys):
    def _headers(customer_id: str | None = None) -> dict[str, str]:
        token, _ = create_access_token(
            settings,
            signing_key=jwt_keys.signing_key,
            subject=customer_id or str(uuid.uuid4()),
            email="c@example.com",
            ttl_seconds=600,
        )
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture
def internal_headers(settings):
    return {INTERNAL_TOKEN_HEADER: settings.internal_token_value(), "X-Caller-Service": "tests"}


@pytest.fixture
def customer():
    return str(uuid.uuid4())


@pytest.fixture
def make_account(client, auth_headers):
    def _make(customer_id: str, account_type: str = "SAVINGS") -> dict:
        response = client.post(
            "/accounts", json={"account_type": account_type}, headers=auth_headers(customer_id)
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _make


@pytest.fixture
def seed(client, internal_headers):
    def _seed(account_id: str, amount: str) -> dict:
        response = client.post(
            f"/internal/dev/accounts/{account_id}/seed",
            json={"amount": amount},
            headers=internal_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _seed

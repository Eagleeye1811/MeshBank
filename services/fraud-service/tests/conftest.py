import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common.security import INTERNAL_TOKEN_HEADER


@pytest.fixture
def settings(database_url_factory) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url_factory("fraud"),
        fraud_review_threshold="50000.00",
        fraud_hard_limit="200000.00",
        fraud_hard_limit_action="REJECT",
        fraud_blocked_payees="Shady Merchant, scam-co",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def headers(settings):
    return {INTERNAL_TOKEN_HEADER: settings.internal_token_value()}


@pytest.fixture
def payload():
    def _payload(**overrides):
        body = {
            "operation_id": "op-00000001",
            "operation_type": "TRANSFER",
            "customer_id": "cust-1",
            "source_account_id": "acc-1",
            "destination_account_id": "acc-2",
            "amount": "100.00",
            "currency": "INR",
        }
        body.update(overrides)
        return body

    return _payload

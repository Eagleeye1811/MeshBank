import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.workflow import TransferWorkflow
from common.security import INTERNAL_TOKEN_HEADER, create_access_token
from tests.fakes import FakeAccounts, FakeFraud, FakeNotifications

ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())
SRC = str(uuid.uuid4())
DST = str(uuid.uuid4())


@pytest.fixture
def settings(database_url_factory) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url_factory("transactions"),
        recovery_interval_seconds=0,
    )


@pytest.fixture
def fakes():
    accounts = FakeAccounts()
    accounts.add(SRC, ALICE, "1000.00")
    accounts.add(DST, BOB, "50.00")
    return accounts, FakeFraud(), FakeNotifications()


def build_client(settings, fakes) -> TestClient:
    app = create_app(settings)
    app.state.account_client, app.state.fraud_client, app.state.notification_client = fakes
    return TestClient(app)


@pytest.fixture
def client(settings, fakes):
    with build_client(settings, fakes) as test_client:
        yield test_client


@pytest.fixture
def accounts(fakes) -> FakeAccounts:
    return fakes[0]


@pytest.fixture
def fraud(fakes) -> FakeFraud:
    return fakes[1]


@pytest.fixture
def notifications(fakes) -> FakeNotifications:
    return fakes[2]


@pytest.fixture
def auth(settings, jwt_keys):
    def _auth(customer_id: str = ALICE) -> dict[str, str]:
        token, _ = create_access_token(
            settings,
            signing_key=jwt_keys.signing_key,
            subject=customer_id,
            email="c@example.com",
            ttl_seconds=600,
        )
        return {"Authorization": f"Bearer {token}"}

    return _auth


@pytest.fixture
def internal_headers(settings):
    return {INTERNAL_TOKEN_HEADER: settings.internal_token_value()}


@pytest.fixture
def transfer(client, auth):
    def _transfer(amount="100.00", customer=ALICE, **overrides):
        body = {
            "source_account_id": SRC,
            "destination_account_id": DST,
            "amount": amount,
            "currency": "INR",
        }
        body.update(overrides)
        return client.post("/transfers", json=body, headers=auth(customer))

    return _transfer


@pytest.fixture
def recover(client, fakes):
    def _recover():
        accounts, fraud, notifications = fakes
        with client.app.state.db.session() as session:
            return TransferWorkflow(
                session,
                accounts=accounts,
                fraud=fraud,
                notifications=notifications,
                lease_seconds=60,
            ).recover()

    return _recover


@pytest.fixture
def expire_leases(client):
    """Simulate time passing beyond the request lease (e.g. after a crash)."""
    from sqlalchemy import update

    from app.models import Transaction

    def _expire():
        with client.app.state.db.session() as session:
            session.execute(update(Transaction).values(lease_until=None))
            session.commit()

    return _expire

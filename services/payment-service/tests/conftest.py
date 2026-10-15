import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.clients import AccountInfo, DebitTransaction, FraudResult
from app.config import Settings
from app.main import create_app, workflow_for
from common.errors import DownstreamError
from common.security import INTERNAL_TOKEN_HEADER, create_access_token

ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())
ACC = str(uuid.uuid4())


def outage(target: str, maybe_processed: bool = False) -> DownstreamError:
    prefix = target.upper().replace("-", "_")
    return DownstreamError(
        f"{target} down", code=f"{prefix}_UNAVAILABLE", maybe_processed=maybe_processed
    )


@dataclass
class FakeAccounts:
    accounts: dict = field(default_factory=lambda: {ACC: ("ACTIVE", ALICE)})
    down: bool = False

    def get_account(self, account_id):
        if self.down:
            raise outage("account-service")
        if account_id not in self.accounts:
            return None
        status, owner = self.accounts[account_id]
        return AccountInfo(id=account_id, customer_id=owner, currency="INR", status=status)


@dataclass
class FakeFraud:
    decision: str = "APPROVE"
    down: bool = False

    def evaluate(self, payload):
        if self.down:
            raise outage("fraud-service")
        return FraudResult(decision=self.decision, reason_codes=["TEST"], explanation="test")


@dataclass
class FakeTransactions:
    """Simulates the transaction service's idempotent payment-debit workflow."""

    balance: Decimal = Decimal("1000.00")
    debits: dict = field(default_factory=dict)  # payment_id -> DebitTransaction
    fail: dict = field(default_factory=lambda: defaultdict(list))
    pending_debits: int = 0  # how many calls report PROCESSING
    calls: list = field(default_factory=list)

    def payment_debit(self, *, payment_id, customer_id, account_id, amount, currency, payee):
        self.calls.append(("debit", payment_id))
        if self.fail["debit"]:
            raise self.fail["debit"].pop(0)
        if payment_id not in self.debits:
            if amount > self.balance:
                self.debits[payment_id] = DebitTransaction(
                    id=f"tx-{payment_id}",
                    status="REJECTED",
                    step="DONE",
                    failure_code="INSUFFICIENT_FUNDS",
                    failure_reason="insufficient",
                )
            else:
                self.balance -= amount
                self.debits[payment_id] = DebitTransaction(
                    id=f"tx-{payment_id}", status="COMPLETED", step="DONE"
                )
            self._amounts = getattr(self, "_amounts", {})
            self._amounts[f"tx-{payment_id}"] = amount
        if self.pending_debits:
            self.pending_debits -= 1
            return DebitTransaction(
                id=f"tx-{payment_id}", status="PROCESSING", step="DEBIT_PENDING"
            )
        return self.debits[payment_id]

    def reverse(self, transaction_id, reason):
        self.calls.append(("reverse", transaction_id))
        if self.fail["reverse"]:
            raise self.fail["reverse"].pop(0)
        payment_id = transaction_id.removeprefix("tx-")
        tx = self.debits[payment_id]
        if tx.status != "REVERSED":
            self.balance += self._amounts[transaction_id]
            self.debits[payment_id] = tx.model_copy(update={"status": "REVERSED"})
        return self.debits[payment_id]


@dataclass
class FakeNotifications:
    down: bool = False
    sent: dict = field(default_factory=dict)

    def notify(self, *, event_key, customer_id, type, message):
        if self.down:
            raise outage("notification-service")
        self.sent[event_key] = {"customer_id": customer_id, "type": type}


@pytest.fixture
def settings(database_url_factory) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url_factory("payments"),
        recovery_interval_seconds=0,
        payment_provider_max_amount="5000.00",
    )


@pytest.fixture
def fakes():
    return FakeAccounts(), FakeFraud(), FakeTransactions(), FakeNotifications()


@pytest.fixture
def client(settings, fakes):
    app = create_app(settings)
    (
        app.state.account_client,
        app.state.fraud_client,
        app.state.transaction_client,
        app.state.notification_client,
    ) = fakes
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(settings, jwt_keys):
    def _auth(customer_id=ALICE):
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
def pay(client, auth):
    def _pay(amount="250.00", customer=ALICE, **overrides):
        body = {
            "account_id": ACC,
            "payment_type": "BILL",
            "payee_name": "City Electricity",
            "payee_reference": "ELEC-12345",
            "amount": amount,
            "currency": "INR",
        }
        body.update(overrides)
        return client.post("/payments", json=body, headers=auth(customer))

    return _pay


@pytest.fixture
def recover(client):
    def _recover():
        from sqlalchemy import update

        from app.models import Payment

        with client.app.state.db.session() as session:
            session.execute(update(Payment).values(lease_until=None))
            session.commit()
            return workflow_for(client.app.state, session).recover()

    return _recover

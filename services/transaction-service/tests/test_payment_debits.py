"""Internal payment-debit workflow used by the payment service."""

from decimal import Decimal

import pytest

from tests.conftest import ALICE, BOB, SRC


@pytest.fixture
def debit(client, internal_headers):
    def _debit(payment_id="pay-00000001", amount="200.00", customer=ALICE, account=SRC):
        return client.post(
            "/internal/payment-debits",
            json={
                "payment_id": payment_id,
                "customer_id": customer,
                "account_id": account,
                "amount": amount,
                "currency": "INR",
                "payee": "Electricity Board",
            },
            headers=internal_headers,
        )

    return _debit


def test_requires_internal_token(client, auth):
    assert client.post("/internal/payment-debits", json={}, headers=auth()).status_code == 403


def test_payment_debit_completes_without_fraud_call_or_notification(
    debit, accounts, fraud, notifications
):
    response = debit()
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "PAYMENT_DEBIT"
    assert body["status"] == "COMPLETED"
    assert body["notification_status"] == "NOT_REQUIRED"
    assert accounts.balance(SRC) == Decimal("800.00")
    assert fraud.calls == []
    assert notifications.sent == {}


def test_payment_debit_is_idempotent(debit, accounts):
    first, second = debit(), debit()
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert accounts.balance(SRC) == Decimal("800.00")
    assert debit(amount="300.00").status_code == 409


def test_payment_debit_insufficient_funds(debit, accounts):
    body = debit(amount="5000.00").json()
    assert body["status"] == "REJECTED"
    assert body["failure_code"] == "INSUFFICIENT_FUNDS"


def test_payment_debit_ownership(debit):
    assert debit(customer=BOB).status_code == 403


def test_payment_debit_retry_resumes_unknown_outcome(debit, accounts, expire_leases):
    accounts.lose_response["debit"] = 1
    first = debit().json()
    assert first["status"] == "PROCESSING"
    expire_leases()
    second = debit().json()
    assert second["status"] == "COMPLETED"
    assert accounts.balance(SRC) == Decimal("800.00")


def test_reversal_refunds_completed_payment_once(client, debit, accounts, internal_headers):
    tx_id = debit().json()["id"]
    for _ in range(2):
        response = client.post(
            f"/internal/transactions/{tx_id}/reversal",
            json={"reason": "Provider declined"},
            headers=internal_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REVERSED"
    assert accounts.balance(SRC) == Decimal("1000.00")


def test_cannot_reverse_rejected_debit(client, debit, internal_headers):
    tx_id = debit(amount="9999.00").json()["id"]
    response = client.post(
        f"/internal/transactions/{tx_id}/reversal", json={"reason": "x"}, headers=internal_headers
    )
    assert response.status_code == 409

from decimal import Decimal

import pytest

from app.provider import SimulatedProvider
from common.errors import ServiceError
from tests.conftest import ALICE, BOB, outage


def test_successful_payment(client, pay, fakes, auth):
    _, _, transactions, notifications = fakes
    response = pay("250.00")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["provider_status"] == "APPROVED"
    assert body["provider_reference"].startswith("SIMPAY-")
    assert body["transaction_id"] == f"tx-{body['id']}"
    assert body["customer_id"] == ALICE
    assert transactions.balance == Decimal("750.00")
    assert notifications.sent[f"payment:{body['id']}:COMPLETED"]["type"] == "PAYMENT_SUCCEEDED"
    assert client.get(f"/payments/{body['id']}", headers=auth()).json()["notification_status"] == (
        "SENT"
    )


def test_merchant_payment_type(pay):
    assert pay(payment_type="MERCHANT", payee_reference="MERCH-77").json()["status"] == "COMPLETED"


@pytest.mark.parametrize("amount", ["0", "-1", "1.005", "x", "99999999999999"])
def test_invalid_amounts(pay, amount):
    assert pay(amount).status_code == 422


def test_invalid_currency_and_payment_type(pay):
    assert pay(currency="EUR").status_code == 422
    assert pay(payment_type="CRYPTO").status_code == 422


def test_insufficient_funds(pay, fakes):
    body = pay("1000.01").json()
    assert body["status"] == "REJECTED"
    assert body["failure_code"] == "INSUFFICIENT_FUNDS"
    assert fakes[2].balance == Decimal("1000.00")
    assert fakes[3].sent[f"payment:{body['id']}:REJECTED"]["type"] == "PAYMENT_FAILED"


def test_fraud_rejection_and_review(pay, fakes):
    fraud, transactions = fakes[1], fakes[2]
    fraud.decision = "REJECT"
    assert pay().json()["failure_code"] == "FRAUD_REJECTED"
    fraud.decision = "REVIEW"
    review = pay()
    assert review.status_code == 202
    assert review.json()["status"] == "UNDER_REVIEW"
    assert transactions.calls == []  # never charged


def test_fraud_service_unavailable(pay, fakes):
    fakes[1].down = True
    response = pay()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "FRAUD_SERVICE_UNAVAILABLE"
    assert fakes[2].calls == []


def test_duplicate_requests_charge_once(pay, fakes):
    first = pay(idempotency_key="pay-key-000001")
    second = pay(idempotency_key="pay-key-000001")
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert fakes[2].balance == Decimal("750.00")
    conflict = pay("10.00", idempotency_key="pay-key-000001")
    assert conflict.status_code == 409


def test_provider_decline_refunds_customer(pay, fakes):
    body = pay(payee_reference="DECLINE-001").json()
    assert body["status"] == "FAILED"
    assert body["failure_code"] == "PROVIDER_DECLINED"
    assert body["provider_status"] == "DECLINED"
    assert "refunded" in body["failure_reason"]
    assert fakes[2].balance == Decimal("1000.00")
    assert ("reverse", f"tx-{body['id']}") in fakes[2].calls


def test_provider_limit_decline(pay, fakes):
    fakes[2].balance = Decimal("100000")
    assert pay("5000.00").json()["status"] == "COMPLETED"  # boundary: not strictly above
    assert pay("5000.01").json()["failure_code"] == "PROVIDER_DECLINED"


def test_refund_outage_is_recovered(pay, fakes, recover):
    fakes[2].fail["reverse"].append(outage("transaction-service", maybe_processed=True))
    body = pay(payee_reference="DECLINE-XYZ").json()
    assert body["status"] == "PROCESSING"
    assert body["step"] == "REFUND_PENDING"
    assert fakes[2].balance == Decimal("750.00")
    recover()
    assert fakes[2].balance == Decimal("1000.00")


def test_unknown_debit_outcome_stays_processing_and_recovers(client, pay, fakes, recover, auth):
    fakes[2].pending_debits = 1
    response = pay()
    assert response.status_code == 202
    assert response.json()["status"] == "PROCESSING"
    report = recover()
    assert report.advanced == 1
    final = client.get(f"/payments/{response.json()['id']}", headers=auth()).json()
    assert final["status"] == "COMPLETED"
    assert fakes[2].balance == Decimal("750.00")  # charged exactly once


def test_transaction_service_unreachable_first_attempt(pay, fakes):
    fakes[2].fail["debit"].append(outage("transaction-service"))
    response = pay()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TRANSACTION_SERVICE_UNAVAILABLE"


def test_transaction_service_refusal_is_rejection(pay, fakes):
    fakes[2].fail["debit"].append(ServiceError("no", code="ACCOUNT_INACTIVE", status_code=409))
    body = pay().json()
    assert body["status"] == "REJECTED" and body["failure_code"] == "ACCOUNT_INACTIVE"


def test_account_ownership_and_existence(pay, fakes):
    assert pay(customer=BOB).status_code == 403
    assert pay(account_id="00000000-0000-0000-0000-000000000000").status_code == 404
    fakes[0].down = True
    assert pay().status_code == 503


def test_notification_outage_does_not_change_outcome(client, pay, fakes, recover, auth):
    fakes[3].down = True
    body = pay().json()
    assert body["status"] == "COMPLETED"
    assert body["notification_status"] == "FAILED"
    fakes[3].down = False
    assert recover().notifications_sent == 1


def test_history_is_owner_only(client, pay, auth):
    payment_id = pay().json()["id"]
    assert client.get(f"/customers/{ALICE}/payments", headers=auth()).json()["total"] == 1
    assert client.get(f"/customers/{ALICE}/payments", headers=auth(BOB)).status_code == 403
    assert client.get(f"/payments/{payment_id}", headers=auth(BOB)).status_code == 403
    assert client.get(f"/payments/{payment_id}").status_code == 401


def test_recovery_endpoint_internal_only(client, auth, internal_headers):
    assert client.post("/internal/recovery/run", headers=auth()).status_code == 403
    assert client.post("/internal/recovery/run", headers=internal_headers).status_code == 200


def test_provider_is_deterministic():
    provider = SimulatedProvider(decline_prefix="DECLINE", max_amount=Decimal("100"))
    a = provider.authorize(payment_id="p1", payee_reference="X", amount=Decimal("1"))
    b = provider.authorize(payment_id="p1", payee_reference="X", amount=Decimal("1"))
    assert a == b and a.approved
    assert not provider.authorize(
        payment_id="p1", payee_reference="decline-1", amount=Decimal("1")
    ).approved

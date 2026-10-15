from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import TransactionEvent
from tests.conftest import ALICE, BOB, DST, SRC
from tests.fakes import unavailable


def test_successful_transfer_moves_money_once(client, transfer, accounts, notifications, auth):
    response = transfer("250.75", description="rent")
    assert response.status_code == 201, response.text
    tx = response.json()
    assert tx["status"] == "COMPLETED"
    assert tx["step"] == "DONE"
    assert tx["amount"] == "250.75"
    assert tx["fraud_decision"] == "APPROVE"
    assert tx["completed_at"] is not None
    assert accounts.balance(SRC) == Decimal("749.25")
    assert accounts.balance(DST) == Decimal("300.75")
    # Notification for the final state, with masked account numbers.
    event = notifications.sent[f"transaction:{tx['id']}:COMPLETED"]
    assert event["type"] == "TRANSFER_SUCCEEDED"
    assert SRC not in event["message"]
    assert tx["notification_status"] == "SENT"
    fetched = client.get(f"/transactions/{tx['id']}", headers=auth()).json()
    assert fetched["notification_status"] == "SENT"
    with client.app.state.db.session() as session:
        steps = [
            e.to_status
            for e in session.scalars(
                select(TransactionEvent)
                .where(TransactionEvent.transaction_id == tx["id"])
                .order_by(TransactionEvent.id)
            )
        ]
    assert steps[0] == "PENDING" and steps[-1] == "COMPLETED"


def test_insufficient_funds_is_rejected_without_moving_money(transfer, accounts, notifications):
    response = transfer("1000.01")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["failure_code"] == "INSUFFICIENT_FUNDS"
    assert accounts.balance(SRC) == Decimal("1000.00")
    assert accounts.balance(DST) == Decimal("50.00")
    assert accounts.applied_count("credit") == 0
    assert notifications.sent[f"transaction:{body['id']}:REJECTED"]["type"] == "TRANSFER_FAILED"


def test_fraud_rejection(transfer, fraud, accounts):
    fraud.decision = "REJECT"
    body = transfer().json()
    assert body["status"] == "REJECTED"
    assert body["failure_code"] == "FRAUD_REJECTED"
    assert body["fraud_reason_codes"] == ["TEST_RULE"]
    assert not any(m == "debit" for m, _ in accounts.calls)


def test_fraud_review_holds_transfer(transfer, fraud, accounts, notifications):
    fraud.decision = "REVIEW"
    response = transfer()
    assert response.status_code == 202
    assert response.json()["status"] == "UNDER_REVIEW"
    assert accounts.balance(SRC) == Decimal("1000.00")
    assert notifications.sent[f"transaction:{response.json()['id']}:UNDER_REVIEW"]["type"] == (
        "FRAUD_REVIEW"
    )


def test_fraud_service_unavailable_fails_closed(client, transfer, fraud, accounts, auth):
    fraud.down = True
    response = transfer()
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "FRAUD_SERVICE_UNAVAILABLE"
    tx = client.get(f"/transactions/{error['details']['transaction_id']}", headers=auth()).json()
    assert tx["status"] == "FAILED"
    assert accounts.balance(SRC) == Decimal("1000.00")


def test_operation_id_sent_to_fraud_is_transaction_id(transfer, fraud):
    body = transfer().json()
    assert fraud.calls[0]["operation_id"] == body["id"]
    assert fraud.calls[0]["customer_id"] == ALICE


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"source_account_id": "missing"}, 404, "SOURCE_ACCOUNT_NOT_FOUND"),
        ({"destination_account_id": "missing"}, 404, "DESTINATION_ACCOUNT_NOT_FOUND"),
        ({"destination_account_id": SRC}, 400, "SAME_ACCOUNT"),
    ],
)
def test_account_validation(client, transfer, overrides, status, code, auth):
    response = transfer(**overrides)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert client.get(f"/customers/{ALICE}/transactions", headers=auth()).json()["total"] == 0


def test_cannot_transfer_from_someone_elses_account(transfer, accounts):
    response = transfer(customer=BOB)
    assert response.status_code == 403
    assert accounts.balance(SRC) == Decimal("1000.00")


def test_inactive_destination_rejected_upfront(transfer, accounts):
    accounts.accounts[DST].status = "FROZEN"
    assert transfer().status_code == 409


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        ("0", "INR"),
        ("-5.00", "INR"),
        ("10.001", "INR"),
        ("abc", "INR"),
        ("10", "USD"),
        ("99999999999999", "INR"),
    ],
)
def test_invalid_amounts_and_currency(transfer, amount, currency):
    response = transfer(amount, currency=currency)
    assert response.status_code == 422


def test_requires_valid_token(client, settings):
    body = {
        "source_account_id": SRC,
        "destination_account_id": DST,
        "amount": "1",
        "currency": "INR",
    }
    assert client.post("/transfers", json=body).status_code == 401
    bad = client.post("/transfers", json=body, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_idempotent_retry_does_not_transfer_twice(transfer, accounts):
    first = transfer("100.00", idempotency_key="key-transfer-0001")
    second = transfer("100.00", idempotency_key="key-transfer-0001")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["id"] == second.json()["id"]
    assert accounts.balance(SRC) == Decimal("900.00")
    assert accounts.balance(DST) == Decimal("150.00")


def test_idempotency_key_reuse_with_different_payload_conflicts(transfer):
    transfer("100.00", idempotency_key="key-transfer-0002")
    response = transfer("200.00", idempotency_key="key-transfer-0002")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_keys_are_scoped_per_customer(transfer, accounts):
    accounts.add("bob-src-0001", BOB, "500.00")
    a = transfer(idempotency_key="shared-key-0001")
    b = transfer(customer=BOB, source_account_id="bob-src-0001", idempotency_key="shared-key-0001")
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_account_service_down_before_transfer(client, transfer, accounts, auth):
    accounts.down = True
    response = transfer()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACCOUNT_SERVICE_UNAVAILABLE"
    assert client.get(f"/customers/{ALICE}/transactions", headers=auth()).json()["total"] == 0


def test_debit_connection_refused_fails_safely(transfer, accounts):
    accounts.fail_before["debit"].append(unavailable())
    response = transfer()
    assert response.status_code == 503
    assert response.json()["error"]["details"]["status"] == "FAILED"
    assert accounts.balance(SRC) == Decimal("1000.00")


def test_notification_service_down_does_not_affect_transfer(
    client, transfer, notifications, recover, auth
):
    notifications.down = True
    response = transfer()
    assert response.status_code == 201
    tx_id = response.json()["id"]
    assert client.get(f"/transactions/{tx_id}", headers=auth()).json()["notification_status"] == (
        "FAILED"
    )
    notifications.down = False
    report = recover()
    assert report.notifications_sent == 1
    assert client.get(f"/transactions/{tx_id}", headers=auth()).json()["notification_status"] == (
        "SENT"
    )
    assert f"transaction:{tx_id}:COMPLETED" in notifications.sent


def test_history_authorization_and_pagination(client, transfer, auth):
    ids = [transfer("1.00").json()["id"] for _ in range(3)]
    page = client.get(f"/customers/{ALICE}/transactions?limit=2&offset=0", headers=auth())
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert [t["id"] for t in page.json()["items"]] == ids[::-1][:2]

    assert client.get(f"/customers/{ALICE}/transactions", headers=auth(BOB)).status_code == 403
    assert client.get(f"/transactions/{ids[0]}", headers=auth(BOB)).status_code == 403
    missing = client.get("/transactions/00000000-0000-0000-0000-000000000000", headers=auth())
    assert missing.status_code == 404

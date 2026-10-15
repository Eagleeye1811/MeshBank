"""Partial failures, compensation and recovery of the transfer workflow."""

from decimal import Decimal

import pytest

from app.models import Transaction
from app.models import TransactionStatus as S
from app.state import InvalidTransition, check_transition
from tests.conftest import ALICE, DST, SRC, build_client
from tests.fakes import timeout


def _tx(client, tx_id) -> Transaction:
    with client.app.state.db.session() as session:
        return session.get(Transaction, tx_id)


def test_lost_debit_response_stays_processing_then_recovers(
    client, transfer, accounts, recover, expire_leases
):
    # The debit is applied but both attempts' responses are lost.
    accounts.lose_response["debit"] = 1
    response = transfer("100.00")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PROCESSING"
    assert body["step"] == "DEBIT_PENDING"
    assert accounts.balance(SRC) == Decimal("900.00")  # debit happened
    assert accounts.balance(DST) == Decimal("50.00")

    expire_leases()
    recover()
    tx = _tx(client, body["id"])
    assert tx.status == S.COMPLETED
    assert accounts.balance(SRC) == Decimal("900.00")  # not debited twice
    assert accounts.balance(DST) == Decimal("150.00")


def test_failure_after_debit_before_credit_is_never_completed(
    client, transfer, accounts, recover, expire_leases, auth
):
    accounts.fail_before["credit"].append(timeout())
    response = transfer("100.00")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PROCESSING"
    assert body["step"] == "CREDIT_PENDING"
    assert "Awaiting recovery" in body["failure_reason"]
    assert accounts.balance(SRC) == Decimal("900.00")
    assert accounts.balance(DST) == Decimal("50.00")

    expire_leases()
    report = recover()
    assert report.advanced == 1
    final = client.get(f"/transactions/{body['id']}", headers=auth()).json()
    assert final["status"] == "COMPLETED"
    assert final["failure_reason"] is None
    assert accounts.balance(DST) == Decimal("150.00")
    assert accounts.applied_count("debit") == 1


def test_credit_rejected_triggers_compensating_reversal(client, transfer, accounts, notifications):
    # Destination is frozen after validation but before the credit.
    original_credit = accounts.credit

    def freeze_then_credit(*args, **kwargs):
        accounts.accounts[DST].status = "FROZEN"
        return original_credit(*args, **kwargs)

    accounts.credit = freeze_then_credit
    response = transfer("100.00")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failure_code"] == "CREDIT_REJECTED_ACCOUNT_INACTIVE"
    assert accounts.balance(SRC) == Decimal("1000.00")  # debit reversed
    assert accounts.balance(DST) == Decimal("50.00")
    assert ("reverse", f"{body['id']}:reversal") in accounts.calls
    assert notifications.sent[f"transaction:{body['id']}:FAILED"]["type"] == "TRANSFER_FAILED"


def test_reversal_outage_is_retried_by_recovery(client, transfer, accounts, recover, expire_leases):
    accounts.accounts[DST].status = "ACTIVE"
    original_credit = accounts.credit

    def freeze_then_credit(*args, **kwargs):
        accounts.accounts[DST].status = "CLOSED"
        return original_credit(*args, **kwargs)

    accounts.credit = freeze_then_credit
    accounts.fail_before["reverse"].append(timeout())
    body = transfer("100.00").json()
    assert body["status"] == "PROCESSING"
    assert body["step"] == "REVERSAL_PENDING"
    assert accounts.balance(SRC) == Decimal("900.00")

    expire_leases()
    recover()
    assert _tx(client, body["id"]).status == S.FAILED
    assert accounts.balance(SRC) == Decimal("1000.00")


def test_service_restart_while_pending(settings, fakes, auth):
    """A transfer left mid-flight survives a restart and is completed by the
    recovery loop of the new process (same database file)."""
    accounts, fraud, notifications = fakes
    accounts.fail_before["credit"].append(timeout())
    with build_client(settings, fakes) as first:
        body = first.post(
            "/transfers",
            json={
                "source_account_id": SRC,
                "destination_account_id": DST,
                "amount": "10.00",
                "currency": "INR",
            },
            headers=auth(),
        ).json()
        assert body["step"] == "CREDIT_PENDING"

    # "Restart": a brand-new app instance on the same database.
    with build_client(settings, fakes) as second:
        from app.recovery import run_once

        run_once(second.app.state)  # leases were released when the request ended
        final = second.get(f"/transactions/{body['id']}", headers=auth()).json()
        assert final["status"] == "COMPLETED"
    assert accounts.balance(DST) == Decimal("60.00")


def test_recovery_skips_transactions_leased_by_inflight_requests(
    client, transfer, accounts, recover
):
    accounts.fail_before["credit"].append(timeout())
    body = transfer().json()
    # Re-acquire a lease as if a request were still executing.
    from datetime import timedelta

    from sqlalchemy import update

    from common.database import utcnow

    with client.app.state.db.session() as session:
        session.execute(update(Transaction).values(lease_until=utcnow() + timedelta(minutes=5)))
        session.commit()
    recover()
    assert _tx(client, body["id"]).status == S.PROCESSING


def test_recovery_endpoint_is_internal_only(client, internal_headers, auth):
    assert client.post("/internal/recovery/run", headers=auth()).status_code == 403
    response = client.post("/internal/recovery/run", headers=internal_headers)
    assert response.status_code == 200
    assert response.json() == {"examined": 0, "advanced": 0, "notifications_sent": 0}


@pytest.mark.parametrize(
    ("current", "target", "ok"),
    [
        (S.PENDING, S.PROCESSING, True),
        (S.PROCESSING, S.COMPLETED, True),
        (S.PROCESSING, S.UNDER_REVIEW, True),
        (S.COMPLETED, S.REVERSED, True),
        (S.PENDING, S.COMPLETED, False),
        (S.COMPLETED, S.FAILED, False),
        (S.REJECTED, S.PROCESSING, False),
        (S.FAILED, S.COMPLETED, False),
    ],
)
def test_state_transitions(current, target, ok):
    if ok:
        check_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            check_transition(current, target)


def test_customer_owner_recorded_from_token(transfer):
    assert transfer().json()["customer_id"] == ALICE

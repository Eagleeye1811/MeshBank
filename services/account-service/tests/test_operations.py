"""Internal idempotent balance operations used by the transfer workflow."""

import threading
from decimal import Decimal

import pytest

from app.service import AccountService


def _op(client, headers, account_id, kind, op_id, amount="100.00", currency="INR"):
    return client.post(
        f"/internal/accounts/{account_id}/{kind}",
        json={"operation_id": op_id, "amount": amount, "currency": currency, "reference": "t1"},
        headers=headers,
    )


def _balance(client, account_id, auth_headers, customer):
    return client.get(f"/accounts/{account_id}/balance", headers=auth_headers(customer)).json()[
        "balance"
    ]


@pytest.fixture
def funded(customer, make_account, seed):
    account = make_account(customer)
    seed(account["id"], "500.00")
    return account


def test_internal_routes_require_internal_token(client, funded):
    response = _op(client, {}, funded["id"], "debit", "op-00000001")
    assert response.status_code == 403
    wrong = _op(client, {"X-Internal-Token": "wrong"}, funded["id"], "debit", "op-00000001")
    assert wrong.status_code == 403


def test_debit_and_credit(client, internal_headers, auth_headers, customer, funded):
    debit = _op(client, internal_headers, funded["id"], "debit", "op-debit-0001", "120.25")
    assert debit.status_code == 200
    assert debit.json()["status"] == "APPLIED"
    assert _balance(client, funded["id"], auth_headers, customer) == "379.75"
    credit = _op(client, internal_headers, funded["id"], "credit", "op-credit-001", "0.25")
    assert credit.json()["status"] == "APPLIED"
    assert _balance(client, funded["id"], auth_headers, customer) == "380.00"


def test_insufficient_funds_rejected_and_recorded(
    client, internal_headers, auth_headers, customer, funded
):
    response = _op(client, internal_headers, funded["id"], "debit", "op-too-much-1", "500.01")
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["rejection_code"] == "INSUFFICIENT_FUNDS"
    assert _balance(client, funded["id"], auth_headers, customer) == "500.00"
    # The rejection is durable: replaying returns the same outcome.
    replay = _op(client, internal_headers, funded["id"], "debit", "op-too-much-1", "500.01")
    assert replay.json()["status"] == "REJECTED"
    assert replay.json()["replayed"] is True


def test_duplicate_operation_is_applied_once(
    client, internal_headers, auth_headers, customer, funded
):
    first = _op(client, internal_headers, funded["id"], "debit", "op-dup-000001", "100.00")
    second = _op(client, internal_headers, funded["id"], "debit", "op-dup-000001", "100.00")
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert _balance(client, funded["id"], auth_headers, customer) == "400.00"


def test_operation_id_reuse_with_different_payload_conflicts(client, internal_headers, funded):
    _op(client, internal_headers, funded["id"], "debit", "op-reuse-0001", "10.00")
    response = _op(client, internal_headers, funded["id"], "debit", "op-reuse-0001", "11.00")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPERATION_ID_CONFLICT"


def test_invalid_amounts_rejected(client, internal_headers, funded):
    for amount in ("0", "-5", "1.001", "abc", "NaN"):
        response = _op(client, internal_headers, funded["id"], "debit", "op-invalid-01", amount)
        assert response.status_code == 422, amount


def test_currency_mismatch_and_inactive_account(client, internal_headers, funded):
    frozen = client.put(
        f"/internal/accounts/{funded['id']}/status",
        json={"status": "FROZEN"},
        headers=internal_headers,
    )
    assert frozen.status_code == 200
    response = _op(client, internal_headers, funded["id"], "credit", "op-frozen-001", "1.00")
    assert response.json()["status"] == "REJECTED"
    assert response.json()["rejection_code"] == "ACCOUNT_INACTIVE"


def test_unknown_account_is_404(client, internal_headers):
    response = _op(client, internal_headers, "missing-account", "debit", "op-missing-01")
    assert response.status_code == 404


def test_reversal_restores_funds_once(client, internal_headers, auth_headers, customer, funded):
    _op(client, internal_headers, funded["id"], "debit", "tx1:debit-000", "200.00")
    body = {"operation_id": "tx1:reversal-0", "original_operation_id": "tx1:debit-000"}
    first = client.post(
        f"/internal/accounts/{funded['id']}/reversals", json=body, headers=internal_headers
    )
    assert first.status_code == 200
    assert first.json()["kind"] == "REVERSAL"
    assert first.json()["amount"] == "200.00"
    replay = client.post(
        f"/internal/accounts/{funded['id']}/reversals", json=body, headers=internal_headers
    )
    assert replay.json()["replayed"] is True
    other = client.post(
        f"/internal/accounts/{funded['id']}/reversals",
        json={"operation_id": "tx1:reversal-2", "original_operation_id": "tx1:debit-000"},
        headers=internal_headers,
    )
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "ALREADY_REVERSED"
    assert _balance(client, funded["id"], auth_headers, customer) == "500.00"


def test_rejected_debit_cannot_be_reversed(client, internal_headers, funded):
    _op(client, internal_headers, funded["id"], "debit", "tx2:debit-000", "9999.00")
    response = client.post(
        f"/internal/accounts/{funded['id']}/reversals",
        json={"operation_id": "tx2:reversal-0", "original_operation_id": "tx2:debit-000"},
        headers=internal_headers,
    )
    assert response.status_code == 409


def test_operation_lookup(client, internal_headers, funded):
    _op(client, internal_headers, funded["id"], "debit", "op-lookup-001", "1.00")
    found = client.get("/internal/operations/op-lookup-001", headers=internal_headers)
    assert found.status_code == 200 and found.json()["status"] == "APPLIED"
    assert (
        client.get("/internal/operations/op-nope-0001", headers=internal_headers).status_code == 404
    )


def test_concurrent_debits_never_overdraw(client, funded):
    """Ten threads each try to debit 100.00 from a 500.00 balance."""
    db = client.app.state.db
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        with db.session() as session:
            result = AccountService(session).debit(
                funded["id"], f"concurrent-{i:04d}", Decimal("100.00"), "INR", None
            )
        with lock:
            outcomes.append(result.operation.status.value)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("APPLIED") == 5
    assert outcomes.count("REJECTED") == 5
    with db.session() as session:
        assert AccountService(session).get_account(funded["id"]).balance == Decimal("0.00")


def test_concurrent_duplicate_operation_applies_once(client, funded):
    db = client.app.state.db
    barrier = threading.Barrier(5)

    def worker() -> None:
        barrier.wait()
        with db.session() as session:
            AccountService(session).debit(
                funded["id"], "same-op-0001", Decimal("50.00"), "INR", None
            )

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with db.session() as session:
        assert AccountService(session).get_account(funded["id"]).balance == Decimal("450.00")

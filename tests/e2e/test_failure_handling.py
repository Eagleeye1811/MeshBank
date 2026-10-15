"""Workflow E: downstream outages and restarts with real processes."""


def _transfer(api, token, src, dst, amount="10.00", **extra):
    return api.call(
        "POST",
        "transaction",
        "/transfers",
        token,
        json={
            "source_account_id": src,
            "destination_account_id": dst,
            "amount": amount,
            "currency": "INR",
            **extra,
        },
    )


def _parties(api):
    _, alice = api.new_customer()
    _, bob = api.new_customer()
    return alice, api.new_account(alice, seed="500.00"), bob, api.new_account(bob)


def test_fraud_service_unavailable(api, stack):
    alice, src, bob, dst = _parties(api)
    stack["fraud"].stop()
    response = _transfer(api, alice, src, dst)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "FRAUD_SERVICE_UNAVAILABLE"
    tx_id = error["details"]["transaction_id"]
    assert api.call("GET", "transaction", f"/transactions/{tx_id}", alice).json()["status"] == (
        "FAILED"
    )
    assert api.balance(alice, src) == "500.00"
    assert api.balance(bob, dst) == "0.00"
    # Liveness of the transaction service is unaffected by its dependency.
    assert api.call("GET", "transaction", "/health").status_code == 200


def test_account_service_unavailable(api, stack):
    alice, src, bob, dst = _parties(api)
    stack["account"].stop()
    response = _transfer(api, alice, src, dst)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACCOUNT_SERVICE_UNAVAILABLE"
    payment = api.call(
        "POST",
        "payment",
        "/payments",
        alice,
        json={
            "account_id": src,
            "payment_type": "BILL",
            "payee_name": "X",
            "payee_reference": "X-1",
            "amount": "1.00",
            "currency": "INR",
        },
    )
    assert payment.status_code == 503
    stack["account"].start()
    assert api.balance(alice, src) == "500.00"
    assert _transfer(api, alice, src, dst).json()["status"] == "COMPLETED"


def test_notification_service_unavailable_then_recovered(api, stack):
    alice, src, bob, dst = _parties(api)
    stack["notification"].stop()
    response = _transfer(api, alice, src, dst)
    assert response.status_code == 201
    tx = response.json()
    assert tx["status"] == "COMPLETED"
    assert tx["notification_status"] == "FAILED"
    stack["notification"].start()

    report = api.internal("POST", "transaction", "/internal/recovery/run").json()
    assert report["notifications_sent"] >= 1
    fetched = api.call("GET", "transaction", f"/transactions/{tx['id']}", alice).json()
    assert fetched["notification_status"] == "SENT"
    customer_id = tx["customer_id"]
    notes = api.call("GET", "notification", f"/customers/{customer_id}/notifications", alice)
    assert notes.json()["items"][0]["type"] == "TRANSFER_SUCCEEDED"


def test_data_survives_service_restarts(api, stack):
    alice, src, bob, dst = _parties(api)
    tx = _transfer(api, alice, src, dst, "25.00", idempotency_key="restart-key-0001").json()
    for name in ("transaction", "account"):
        stack[name].stop()
        stack[name].start()
    fetched = api.call("GET", "transaction", f"/transactions/{tx['id']}", alice).json()
    assert fetched["status"] == "COMPLETED"
    # The idempotency record also survived the restart.
    replay = _transfer(api, alice, src, dst, "25.00", idempotency_key="restart-key-0001")
    assert replay.status_code == 200
    assert api.balance(alice, src) == "475.00"
    assert api.balance(bob, dst) == "25.00"


def test_internal_endpoints_require_internal_token(api):
    alice, src, _, _ = _parties(api)
    debit = api.call(
        "POST",
        "account",
        f"/internal/accounts/{src}/debit",
        alice,
        json={"operation_id": "attack-000001", "amount": "1.00", "currency": "INR"},
    )
    assert debit.status_code == 403
    seed = api.call(
        "POST",
        "account",
        f"/internal/dev/accounts/{src}/seed",
        alice,
        json={"amount": "1000000.00"},
    )
    assert seed.status_code == 403
    assert api.balance(alice, src) == "500.00"

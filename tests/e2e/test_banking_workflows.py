"""Cross-service workflows over real HTTP between six running services."""

from datetime import UTC, datetime, timedelta

import jwt


def test_workflow_a_registration_login_and_identity(api):
    customer_id, token = api.new_customer()
    me = api.call("GET", "auth", "/auth/me", token)
    assert me.status_code == 200
    assert me.json()["id"] == customer_id
    assert api.call("POST", "auth", "/auth/verify", json={"token": token}).json()["valid"] is True


def test_workflow_b_accounts_and_ownership(api):
    _, alice = api.new_customer()
    _, mallory = api.new_customer()
    account_id = api.new_account(alice)
    details = api.call("GET", "account", f"/accounts/{account_id}", alice)
    assert details.status_code == 200
    assert details.json()["currency"] == "INR"
    assert api.balance(alice, account_id) == "0.00"
    assert api.call("GET", "account", f"/accounts/{account_id}", mallory).status_code == 403
    assert api.call("GET", "account", f"/accounts/{account_id}/balance", mallory).status_code == 403


def test_workflow_c_transfer_end_to_end(api):
    alice_id, alice = api.new_customer()
    bob_id, bob = api.new_customer()
    src = api.new_account(alice, seed="10000.00")
    dst = api.new_account(bob)

    body = {
        "source_account_id": src,
        "destination_account_id": dst,
        "amount": "2500.50",
        "currency": "INR",
        "idempotency_key": "e2e-transfer-0001",
    }
    created = api.call("POST", "transaction", "/transfers", alice, json=body)
    assert created.status_code == 201, created.text
    tx = created.json()
    assert tx["status"] == "COMPLETED"
    assert tx["fraud_decision"] == "APPROVE"
    assert api.balance(alice, src) == "7499.50"
    assert api.balance(bob, dst) == "2500.50"

    # Retry with the same idempotency key: no second transfer.
    replay = api.call("POST", "transaction", "/transfers", alice, json=body)
    assert replay.status_code == 200
    assert replay.json()["id"] == tx["id"]
    assert api.balance(alice, src) == "7499.50"
    assert api.balance(bob, dst) == "2500.50"

    # Details, history and notifications.
    fetched = api.call("GET", "transaction", f"/transactions/{tx['id']}", alice).json()
    assert fetched["notification_status"] == "SENT"
    history = api.call("GET", "transaction", f"/customers/{alice_id}/transactions", alice).json()
    assert [t["id"] for t in history["items"]] == [tx["id"]]
    notes = api.call("GET", "notification", f"/customers/{alice_id}/notifications", alice).json()
    assert notes["total"] == 1
    assert notes["items"][0]["type"] == "TRANSFER_SUCCEEDED"
    assert notes["items"][0]["delivery_status"] == "DELIVERED_SIMULATED"
    assert src not in notes["items"][0]["message"]  # account IDs are masked

    # Other customers cannot see any of it.
    assert api.call("GET", "transaction", f"/transactions/{tx['id']}", bob).status_code == 403
    assert (
        api.call("GET", "transaction", f"/customers/{alice_id}/transactions", bob).status_code
        == 403
    )
    assert (
        api.call(
            "GET", "notification", f"/notifications/{notes['items'][0]['id']}", bob
        ).status_code
        == 403
    )
    assert bob_id != alice_id


def test_transfer_rejections(api):
    _, alice = api.new_customer()
    _, bob = api.new_customer()
    src = api.new_account(alice, seed="100.00")
    dst = api.new_account(bob)

    def transfer(amount, **extra):
        return api.call(
            "POST",
            "transaction",
            "/transfers",
            alice,
            json={
                "source_account_id": src,
                "destination_account_id": dst,
                "amount": amount,
                "currency": "INR",
                **extra,
            },
        )

    funds = transfer("100.01").json()
    assert funds["status"] == "REJECTED" and funds["failure_code"] == "INSUFFICIENT_FUNDS"
    fraud = transfer("250000.00").json()
    assert fraud["status"] == "REJECTED" and fraud["failure_code"] == "FRAUD_REJECTED"
    assert "HARD_LIMIT_EXCEEDED" in fraud["fraud_reason_codes"]
    review = transfer("60000.00")
    assert review.status_code == 202 and review.json()["status"] == "UNDER_REVIEW"
    assert transfer("0").status_code == 422
    assert transfer("1.234").status_code == 422
    # Bob cannot move Alice's money.
    stolen = api.call(
        "POST",
        "transaction",
        "/transfers",
        bob,
        json={
            "source_account_id": src,
            "destination_account_id": dst,
            "amount": "1.00",
            "currency": "INR",
        },
    )
    assert stolen.status_code == 403
    assert api.balance(alice, src) == "100.00"
    assert api.balance(bob, dst) == "0.00"


def test_workflow_d_payment_end_to_end(api):
    customer_id, token = api.new_customer()
    account = api.new_account(token, seed="5000.00")
    body = {
        "account_id": account,
        "payment_type": "BILL",
        "payee_name": "City Water",
        "payee_reference": "WATER-001",
        "amount": "1200.00",
        "currency": "INR",
        "idempotency_key": "e2e-payment-0001",
    }
    created = api.call("POST", "payment", "/payments", token, json=body)
    assert created.status_code == 201, created.text
    payment = created.json()
    assert payment["status"] == "COMPLETED"
    assert payment["provider_reference"].startswith("SIMPAY-")
    assert api.balance(token, account) == "3800.00"

    duplicate = api.call("POST", "payment", "/payments", token, json=body)
    assert duplicate.status_code == 200 and duplicate.json()["id"] == payment["id"]
    assert api.balance(token, account) == "3800.00"

    history = api.call("GET", "payment", f"/customers/{customer_id}/payments", token).json()
    assert history["total"] == 1
    assert api.call("GET", "payment", f"/payments/{payment['id']}", token).status_code == 200
    # The debit is recorded in the customer's transaction history.
    txs = api.call("GET", "transaction", f"/customers/{customer_id}/transactions", token).json()
    assert txs["items"][0]["kind"] == "PAYMENT_DEBIT"
    assert txs["items"][0]["id"] == payment["transaction_id"]
    notes = api.call("GET", "notification", f"/customers/{customer_id}/notifications", token).json()
    assert [n["type"] for n in notes["items"]] == ["PAYMENT_SUCCEEDED"]


def test_payment_provider_decline_is_refunded_across_services(api):
    _, token = api.new_customer()
    account = api.new_account(token, seed="1000.00")
    response = api.call(
        "POST",
        "payment",
        "/payments",
        token,
        json={
            "account_id": account,
            "payment_type": "MERCHANT",
            "payee_name": "Bad Shop",
            "payee_reference": "DECLINE-42",
            "amount": "300.00",
            "currency": "INR",
        },
    )
    body = response.json()
    assert body["status"] == "FAILED" and body["failure_code"] == "PROVIDER_DECLINED"
    assert api.balance(token, account) == "1000.00"
    tx = api.call("GET", "transaction", f"/transactions/{body['transaction_id']}", token).json()
    assert tx["status"] == "REVERSED"


def test_payment_insufficient_funds(api):
    _, token = api.new_customer()
    account = api.new_account(token, seed="10.00")
    body = api.call(
        "POST",
        "payment",
        "/payments",
        token,
        json={
            "account_id": account,
            "payment_type": "BILL",
            "payee_name": "Gas",
            "payee_reference": "GAS-1",
            "amount": "10.01",
            "currency": "INR",
        },
    ).json()
    assert body["status"] == "REJECTED" and body["failure_code"] == "INSUFFICIENT_FUNDS"


def test_invalid_and_expired_tokens_rejected_everywhere(api, stack):
    customer_id, token = api.new_customer()
    expired = jwt.encode(
        {
            "sub": customer_id,
            "iss": "meshbank-auth",
            "aud": "meshbank-api",
            "jti": "x",
            "typ": "access",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "nbf": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        stack.signing_key.private_key,
        algorithm="RS256",
        headers={"kid": stack.signing_key.kid},
    )
    for service, path in [
        ("auth", "/auth/me"),
        ("account", f"/customers/{customer_id}/accounts"),
        ("transaction", f"/customers/{customer_id}/transactions"),
        ("payment", f"/customers/{customer_id}/payments"),
        ("notification", f"/customers/{customer_id}/notifications"),
    ]:
        assert api.call("GET", service, path, "garbage").status_code == 401, service
        response = api.call("GET", service, path, expired)
        assert response.status_code == 401, service
        assert response.json()["error"]["code"] == "TOKEN_EXPIRED"
    # Customer tokens cannot reach internal endpoints.
    assert api.call("POST", "fraud", "/evaluate", token, json={}).status_code == 403
    assert api.call("POST", "notification", "/notifications", token, json={}).status_code == 403


def test_forged_signature_rejected_everywhere_and_jwks_is_public_only(api, stack, tmp_path):
    from common.keys import generate_key_pair

    customer_id, _ = api.new_customer()
    attacker = generate_key_pair(tmp_path, stack.signing_key.kid, bits=2048)  # same kid
    forged = jwt.encode(
        {
            "sub": customer_id,
            "iss": "meshbank-auth",
            "aud": "meshbank-api",
            "jti": "forged",
            "typ": "access",
            "iat": int(datetime.now(UTC).timestamp()),
            "nbf": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        attacker.private_key,
        algorithm="RS256",
        headers={"kid": attacker.kid},
    )
    for service, path in [
        ("auth", "/auth/me"),
        ("account", f"/customers/{customer_id}/accounts"),
        ("transaction", f"/customers/{customer_id}/transactions"),
        ("payment", f"/customers/{customer_id}/payments"),
        ("notification", f"/customers/{customer_id}/notifications"),
    ]:
        response = api.call("GET", service, path, forged)
        assert response.status_code == 401, service
        assert response.json()["error"]["code"] == "INVALID_TOKEN", service
    keys = api.call("GET", "auth", "/.well-known/jwks.json").json()["keys"]
    assert [k["kid"] for k in keys] == [stack.signing_key.kid]
    assert not {"d", "p", "q", "dp", "dq", "qi"} & set(keys[0])


def test_health_endpoints(api):
    for name in ("auth", "account", "transaction", "payment", "fraud", "notification"):
        assert api.call("GET", name, "/health").json()["status"] == "ok"
        assert api.call("GET", name, "/ready").json()["checks"]["database"] == "ok"

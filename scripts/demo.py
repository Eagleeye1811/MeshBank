"""Seed demo data and walk through the Phase 1 workflows against a running stack.

    python scripts/demo.py                 # services on localhost:8001-8006
    MESHBANK_HOST=10.0.0.5 python scripts/demo.py

DEVELOPMENT ONLY: it funds an account through the dev seeding endpoint, which
exists only when the Account Service runs with DEV_SEED_ENABLED=true and
APP_ENV != production. It uses INTERNAL_API_TOKEN from the environment or the
built-in development token.
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx

HOST = os.environ.get("MESHBANK_HOST", "localhost")
PORTS = {
    "auth": 8001,
    "account": 8002,
    "transaction": 8003,
    "payment": 8004,
    "fraud": 8005,
    "notification": 8006,
}
DEV_INTERNAL_TOKEN = "dev-only-insecure-internal-token-do-not-use-in-production"
INTERNAL_TOKEN = os.environ.get("INTERNAL_API_TOKEN") or DEV_INTERNAL_TOKEN
PASSWORD = "DemoPassw0rd-2026"

http = httpx.Client(timeout=30)


def url(service: str, path: str) -> str:
    return f"http://{HOST}:{PORTS[service]}{path}"


def call(method: str, service: str, path: str, token: str | None = None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = http.request(method, url(service, path), headers=headers, **kwargs)
    return response


def expect(response: httpx.Response, *codes: int) -> dict:
    if response.status_code not in codes:
        print(f"  !! {response.request.method} {response.request.url} -> {response.status_code}")
        print(f"     {response.text}")
        sys.exit(1)
    return response.json()


def step(title: str) -> None:
    print(f"\n== {title}")


def customer(name: str) -> tuple[str, str]:
    email = f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.com"
    user = expect(
        call(
            "POST",
            "auth",
            "/auth/register",
            json={"email": email, "password": PASSWORD, "full_name": name},
        ),
        201,
    )
    token = expect(
        call("POST", "auth", "/auth/login", json={"email": email, "password": PASSWORD}), 200
    )["access_token"]
    print(f"  {name}: {email} (customer_id {user['id']})")
    return user["id"], token


def main() -> int:
    step("Health")
    for name in PORTS:
        ready = call("GET", name, "/ready")
        print(f"  {name:<13} {ready.status_code} {ready.json().get('status')}")
        if ready.status_code != 200:
            return 1

    step("Workflow A: register and log in")
    alice_id, alice = customer("Alice")
    bob_id, bob = customer("Bob")
    print(f"  /auth/me -> {expect(call('GET', 'auth', '/auth/me', alice), 200)['email']}")

    step("Workflow B: accounts")
    src = expect(
        call("POST", "account", "/accounts", alice, json={"account_type": "SAVINGS"}), 201
    )["id"]
    dst = expect(call("POST", "account", "/accounts", bob, json={"account_type": "CURRENT"}), 201)[
        "id"
    ]
    print(f"  Alice account {src}\n  Bob account   {dst}")
    print(
        f"  Bob reading Alice's account -> "
        f"{call('GET', 'account', f'/accounts/{src}', bob).status_code} (expected 403)"
    )

    step("Development-only seed: INR 25000.00 into Alice's account")
    expect(
        http.post(
            url("account", f"/internal/dev/accounts/{src}/seed"),
            json={"amount": "25000.00"},
            headers={"X-Internal-Token": INTERNAL_TOKEN},
        ),
        200,
    )
    print(f"  balance: {expect(call('GET', 'account', f'/accounts/{src}/balance', alice), 200)}")

    step("Workflow C: transfer INR 1500.00 (with idempotency key)")
    body = {
        "source_account_id": src,
        "destination_account_id": dst,
        "amount": "1500.00",
        "currency": "INR",
        "description": "Demo transfer",
        "idempotency_key": f"demo-{uuid.uuid4().hex}",
    }
    tx = expect(call("POST", "transaction", "/transfers", alice, json=body), 201, 202)
    print(f"  transaction {tx['id']}: {tx['status']}")
    replay = call("POST", "transaction", "/transfers", alice, json=body)
    print(
        f"  retry with same key -> {replay.status_code}, same id: {replay.json()['id'] == tx['id']}"
    )
    for who, token, acc in (("Alice", alice, src), ("Bob", bob, dst)):
        bal = expect(call("GET", "account", f"/accounts/{acc}/balance", token), 200)["balance"]
        print(f"  {who} balance: {bal}")

    step("Workflow D: simulated bill payment INR 999.99")
    payment = expect(
        call(
            "POST",
            "payment",
            "/payments",
            alice,
            json={
                "account_id": src,
                "payment_type": "BILL",
                "payee_name": "City Electricity",
                "payee_reference": "ELEC-1001",
                "amount": "999.99",
                "currency": "INR",
                "idempotency_key": f"demo-pay-{uuid.uuid4().hex}",
            },
        ),
        201,
        202,
    )
    print(f"  payment {payment['id']}: {payment['status']} ({payment['provider_reference']})")

    step("Fraud rules: INR 60000.00 transfer is held for review")
    review = call(
        "POST",
        "transaction",
        "/transfers",
        alice,
        json={
            "source_account_id": src,
            "destination_account_id": dst,
            "amount": "60000.00",
            "currency": "INR",
        },
    )
    print(f"  -> {review.status_code} {review.json().get('status')}")

    step("History and notifications for Alice")
    txs = expect(call("GET", "transaction", f"/customers/{alice_id}/transactions", alice), 200)
    for t in txs["items"]:
        print(f"  tx  {t['kind']:<14} {t['amount']:>10} {t['status']}")
    notes = expect(call("GET", "notification", f"/customers/{alice_id}/notifications", alice), 200)
    for n in notes["items"]:
        print(f"  msg {n['type']:<18} {n['delivery_status']}: {n['message']}")

    print("\nDemo complete. Use these tokens to explore further:")
    print(f"  ALICE_TOKEN={alice}\n  ALICE_ACCOUNT={src}\n  BOB_ACCOUNT={dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

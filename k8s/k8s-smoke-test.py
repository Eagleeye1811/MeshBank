"""
MeshBank Kubernetes smoke test.
Runs against NodePort services on localhost (ports 30001-30006).
Mirrors scripts/demo.py but targets the k8s cluster.

Usage:
    make k8s-test            (or: .venv/bin/python k8s/k8s-smoke-test.py)

Every service-to-service hop in these workflows (transaction -> account/fraud/
notification, payment -> account/transaction/fraud/notification) goes through
the mesh sidecars over mutual TLS.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from mesh_topology import kube_context

AUTH_PORT = int(os.environ.get("AUTH_PORT", 30001))
ACCT_PORT = int(os.environ.get("ACCT_PORT", 30002))
TXN_PORT = int(os.environ.get("TXN_PORT", 30003))
PAY_PORT = int(os.environ.get("PAY_PORT", 30004))
FRAUD_PORT = int(os.environ.get("FRAUD_PORT", 30005))
NOTIF_PORT = int(os.environ.get("NOTIF_PORT", 30006))
HOST = os.environ.get("MESHBANK_HOST", "localhost")
PASSWORD = "Sm0keT3st-k8s!"  # noqa: S105 - throwaway test users

http = httpx.Client(timeout=30)

SERVICES = {
    "auth": f"http://{HOST}:{AUTH_PORT}",
    "account": f"http://{HOST}:{ACCT_PORT}",
    "transaction": f"http://{HOST}:{TXN_PORT}",
    "payment": f"http://{HOST}:{PAY_PORT}",
    "fraud": f"http://{HOST}:{FRAUD_PORT}",
    "notification": f"http://{HOST}:{NOTIF_PORT}",
}


def step(title: str) -> None:
    print(f"\n== {title}")


def expect(r: httpx.Response, *codes: int) -> dict:
    if r.status_code not in codes:
        print(f"  !! {r.request.method} {r.request.url} -> {r.status_code}")
        print(f"     {r.text[:400]}")
        sys.exit(1)
    return r.json()


def get(svc: str, path: str, token: str | None = None) -> httpx.Response:
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return http.get(SERVICES[svc] + path, headers=h)


def post(svc: str, path: str, token: str | None = None, **kw) -> httpx.Response:
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return http.post(SERVICES[svc] + path, headers=h, **kw)


def main() -> int:
    step("1. Health check — all services")
    all_ok = True
    for name, base in SERVICES.items():
        r = http.get(base + "/ready")
        status = r.json().get("status", "?") if r.status_code == 200 else "NOT READY"
        symbol = "[OK]  " if r.status_code == 200 else "[FAIL]"
        print(f"  {symbol} {name:<14} {r.status_code}  {status}")
        if r.status_code != 200:
            all_ok = False
    if not all_ok:
        print("\n  One or more services not ready. Check pod status:")
        print("  kubectl get pods -n meshbank")
        return 1

    step("2. Register users Alice and Bob")
    a_email = f"alice-{uuid.uuid4().hex[:6]}@example.com"
    b_email = f"bob-{uuid.uuid4().hex[:6]}@example.com"
    alice_id = expect(
        post(
            "auth",
            "/auth/register",
            json={"email": a_email, "password": PASSWORD, "full_name": "Alice K8s"},
        ),
        201,
    )["id"]
    alice_tok = expect(
        post("auth", "/auth/login", json={"email": a_email, "password": PASSWORD}), 200
    )["access_token"]
    bob_id = expect(
        post(
            "auth",
            "/auth/register",
            json={"email": b_email, "password": PASSWORD, "full_name": "Bob K8s"},
        ),
        201,
    )["id"]
    bob_tok = expect(
        post("auth", "/auth/login", json={"email": b_email, "password": PASSWORD}), 200
    )["access_token"]
    print(f"  Alice: {a_email} ({alice_id})")
    print(f"  Bob:   {b_email} ({bob_id})")

    step("3. Create accounts")
    src = expect(post("account", "/accounts", alice_tok, json={"account_type": "SAVINGS"}), 201)[
        "id"
    ]
    dst = expect(post("account", "/accounts", bob_tok, json={"account_type": "CURRENT"}), 201)["id"]
    print(f"  Alice account: {src}")
    print(f"  Bob   account: {dst}")

    step("4. Authorization check (Bob cannot see Alice's account)")
    r403 = get("account", f"/accounts/{src}", bob_tok)
    print(f"  Bob reads Alice's account -> {r403.status_code} (expected 403)")
    assert r403.status_code == 403, f"Expected 403, got {r403.status_code}"

    step("5. Seed INR 20000 into Alice's account (dev-only CLI inside the account pod)")
    # /internal/* is mTLS-only in the mesh, so seed with the service's own CLI.
    seed = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context("account-service"),
            "exec",
            "-n",
            "meshbank",
            "deployment/account-service",
            "-c",
            "account-service",
            "--",
            "python",
            "-m",
            "app.dev_seed",
            "--account-id",
            src,
            "--amount",
            "20000.00",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if seed.returncode != 0:
        print(f"  !! seeding failed: {seed.stderr.strip() or seed.stdout.strip()}")
        return 1
    bal = expect(get("account", f"/accounts/{src}/balance", alice_tok), 200)
    print(f"  Alice balance: {bal['balance']} {bal['currency']}")

    step("6. Transfer INR 2000 from Alice to Bob (Workflow C)")
    idem_key = f"k8s-demo-{uuid.uuid4().hex}"
    txn = expect(
        post(
            "transaction",
            "/transfers",
            alice_tok,
            json={
                "source_account_id": src,
                "destination_account_id": dst,
                "amount": "2000.00",
                "currency": "INR",
                "description": "K8s smoke test transfer",
                "idempotency_key": idem_key,
            },
        ),
        201,
    )
    print(f"  Transaction {txn['id']}: {txn['status']}")
    assert txn["status"] == "COMPLETED", f"Expected COMPLETED, got {txn['status']}"

    # Verify idempotency
    txn2 = expect(
        post(
            "transaction",
            "/transfers",
            alice_tok,
            json={
                "source_account_id": src,
                "destination_account_id": dst,
                "amount": "2000.00",
                "currency": "INR",
                "description": "K8s smoke test transfer",
                "idempotency_key": idem_key,
            },
        ),
        200,
    )
    assert txn2["id"] == txn["id"], "Idempotency failed: different IDs returned"
    print("  Idempotency check: same ID returned [OK]")

    bal_a = expect(get("account", f"/accounts/{src}/balance", alice_tok), 200)["balance"]
    bal_b = expect(get("account", f"/accounts/{dst}/balance", bob_tok), 200)["balance"]
    print(f"  Alice balance: {bal_a} INR  (expected 18000.00)")
    print(f"  Bob   balance: {bal_b} INR  (expected  2000.00)")
    assert bal_a == "18000.00", f"Unexpected Alice balance: {bal_a}"
    assert bal_b == "2000.00", f"Unexpected Bob balance: {bal_b}"

    step("7. Bill payment INR 500 (Workflow D)")
    pay = expect(
        post(
            "payment",
            "/payments",
            alice_tok,
            json={
                "account_id": src,
                "payment_type": "BILL",
                "payee_name": "State Electricity Board",
                "payee_reference": "ELECTRIC-BILL-001",
                "amount": "500.00",
                "currency": "INR",
                "idempotency_key": f"k8s-pay-{uuid.uuid4().hex}",
            },
        ),
        201,
    )
    print(f"  Payment {pay['id']}: {pay['status']}")
    assert pay["status"] == "COMPLETED", f"Expected COMPLETED, got {pay['status']}"

    step("8. Check transaction history")
    hist = expect(get("transaction", f"/customers/{alice_id}/transactions", alice_tok), 200)
    print(f"  {len(hist['items'])} transaction(s) in history")
    for item in hist["items"]:
        print(f"    {item.get('kind', 'TRANSACTION'):<20} {item['amount']:>10} {item['status']}")

    step("9. Check notifications for Alice")
    notifs = http.get(
        SERVICES["notification"] + f"/customers/{alice_id}/notifications",
        headers={"Authorization": f"Bearer {alice_tok}"},
    )
    msgs = expect(notifs, 200).get("items", [])
    print(f"  {len(msgs)} notification(s) delivered")
    for m in msgs[:3]:
        print(f"    {m.get('type', '?'):<25} {m.get('status', '?')}")
    # One for the transfer, one for the payment: both reached notification-service via mTLS.
    assert len(msgs) >= 2, f"Expected at least 2 notifications, got {len(msgs)}"

    print(f"\n{'='*60}")
    print("  MeshBank Kubernetes smoke test PASSED")
    print(f"{'='*60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

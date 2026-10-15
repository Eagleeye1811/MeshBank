"""Phase 2A acceptance: a SQLite-backed stack's data is migrated to PostgreSQL
with the documented commands, then the stack runs on PostgreSQL with all
balances, idempotency records, payments, fraud decisions and notifications
intact. Requires MESHBANK_TEST_POSTGRES_URL (skipped otherwise)."""

import json
import subprocess
import sys

import pytest

from tests.e2e.harness import ROOT, SERVICES, Api, Stack

pytestmark = pytest.mark.skipif(
    __import__("common.testing", fromlist=["x"]).postgres_admin_url() is None,
    reason="set MESHBANK_TEST_POSTGRES_URL to run PostgreSQL migration tests",
)


def _run(service: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT / "services" / f"{service}-service",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_sqlite_stack_data_migrates_to_postgres(tmp_path, postgres_databases, stack):
    # Pause the session stack so ports and CPU are free; it is restarted afterwards.
    stack.stop_all()
    legacy = Stack(tmp_path / "legacy")
    legacy.tmp.mkdir()
    legacy.start_all()
    api = Api(legacy)
    try:
        email_user_id, token = api.new_customer()
        _, bob = api.new_customer()
        src = api.new_account(token, seed="5000.00")
        dst = api.new_account(bob)
        transfer = {
            "source_account_id": src,
            "destination_account_id": dst,
            "amount": "1250.25",
            "currency": "INR",
            "idempotency_key": "migrate-transfer-0001",
        }
        tx = api.call("POST", "transaction", "/transfers", token, json=transfer).json()
        payment_body = {
            "account_id": src,
            "payment_type": "BILL",
            "payee_name": "Water",
            "payee_reference": "W-1",
            "amount": "100.00",
            "currency": "INR",
            "idempotency_key": "migrate-payment-0001",
        }
        payment = api.call("POST", "payment", "/payments", token, json=payment_body).json()
        assert tx["status"] == payment["status"] == "COMPLETED"
        login_email = api.call("GET", "auth", "/auth/me", token).json()["email"]
    finally:
        legacy.stop_all()

    # Documented procedure: create empty databases, migrate them, import, verify.
    pg_urls = {name: postgres_databases.create() for name in SERVICES}
    for name in SERVICES:
        env = {"PATH": "/usr/bin:/bin", "DATABASE_URL": pg_urls[name]}
        migrated = _run(name, ["-m", "alembic", "upgrade", "head"], env)
        assert migrated.returncode == 0, migrated.stderr
        imported = _run(
            name,
            ["-m", "common.sqlite_import", "--source", legacy.database_urls[name]],
            env,
        )
        assert imported.returncode == 0, imported.stderr
        report = json.loads(imported.stdout)
        assert report["committed"] is True
        assert all(t["digest_match"] for t in report["tables"].values()), name

    migrated_stack = Stack(tmp_path / "pg", database_urls=pg_urls, secrets_env=legacy.secrets_env)
    migrated_stack.tmp.mkdir()
    migrated_stack.start_all()
    api = Api(migrated_stack)
    try:
        # Users and password hashes migrated: the same credentials log in.
        login = api.call(
            "POST",
            "auth",
            "/auth/login",
            json={"email": login_email, "password": "Sup3rSecretPassw0rd"},
        )
        assert login.status_code == 200
        assert api.balance(token, src) == "3649.75"
        assert api.balance(bob, dst) == "1250.25"
        # Idempotency survived for both workflows.
        replay = api.call("POST", "transaction", "/transfers", token, json=transfer)
        assert replay.status_code == 200 and replay.json()["id"] == tx["id"]
        pay_replay = api.call("POST", "payment", "/payments", token, json=payment_body)
        assert pay_replay.status_code == 200 and pay_replay.json()["id"] == payment["id"]
        assert api.balance(token, src) == "3649.75"
        # Fraud decisions are stable: re-evaluating the migrated operation replays.
        fraud = api.internal(
            "POST",
            "fraud",
            "/evaluate",
            json={
                "operation_id": tx["id"],
                "operation_type": "TRANSFER",
                "customer_id": email_user_id,
                "source_account_id": src,
                "destination_account_id": dst,
                "amount": "1250.25",
                "currency": "INR",
            },
        )
        assert fraud.json()["replayed"] is True and fraud.json()["decision"] == "APPROVE"
        notes = api.call(
            "GET", "notification", f"/customers/{email_user_id}/notifications", token
        ).json()
        assert {n["type"] for n in notes["items"]} == {"TRANSFER_SUCCEEDED", "PAYMENT_SUCCEEDED"}
        # Restart persistence on PostgreSQL.
        for name in ("transaction", "account"):
            migrated_stack[name].stop()
            migrated_stack[name].start()
        assert (
            api.call("GET", "transaction", f"/transactions/{tx['id']}", token).json()["status"]
            == "COMPLETED"
        )
        assert api.balance(token, src) == "3649.75"
    finally:
        migrated_stack.stop_all()
        api.http.close()

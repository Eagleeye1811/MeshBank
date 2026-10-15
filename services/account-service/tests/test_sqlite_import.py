"""SQLite demo data -> PostgreSQL import (requires MESHBANK_TEST_POSTGRES_URL)."""

import sqlite3
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import SERVICE_DIR, create_app
from app.models import Base
from app.service import AccountService
from common.migrations import upgrade_to_head
from common.sqlite_import import ImportRefused, VerificationFailed, import_database


def _settings(url: str) -> Settings:
    return Settings(_env_file=None, app_env="test", database_url=url, dev_seed_enabled=True)


@pytest.fixture
def sqlite_source(tmp_path, auth_headers, internal_headers):
    """A Phase-1 style SQLite database with accounts, seeds, debits and replays."""
    url = f"sqlite:///{tmp_path}/legacy-accounts.db"
    customer = str(uuid.uuid4())
    with TestClient(create_app(_settings(url))) as client:
        ids = []
        for _ in range(3):
            ids.append(
                client.post(
                    "/accounts", json={"account_type": "SAVINGS"}, headers=auth_headers(customer)
                ).json()["id"]
            )
        client.post(
            f"/internal/dev/accounts/{ids[0]}/seed",
            json={"amount": "1000.00"},
            headers=internal_headers,
        )
        op = {"operation_id": "tx-legacy:debit", "amount": "250.50", "currency": "INR"}
        client.post(f"/internal/accounts/{ids[0]}/debit", json=op, headers=internal_headers)
        op2 = {"operation_id": "tx-legacy:credit", "amount": "250.50", "currency": "INR"}
        client.post(f"/internal/accounts/{ids[1]}/credit", json=op2, headers=internal_headers)
        rejected = {"operation_id": "tx-big:debit", "amount": "99999.00", "currency": "INR"}
        client.post(f"/internal/accounts/{ids[2]}/debit", json=rejected, headers=internal_headers)
    return {"url": url, "path": tmp_path / "legacy-accounts.db", "customer": customer, "ids": ids}


@pytest.fixture
def target(postgres_url_factory):
    url = postgres_url_factory("import_target")
    upgrade_to_head(SERVICE_DIR, url)
    return url


def _import(source_url, target_url, dry_run=False):
    return import_database(
        metadata=Base.metadata,
        service_dir=SERVICE_DIR,
        source_url=source_url,
        target_url=target_url,
        dry_run=dry_run,
    )


def test_import_copies_and_verifies_everything(sqlite_source, target, internal_headers):
    report = _import(sqlite_source["url"], target)
    assert report.committed
    assert report.tables["accounts"] == {"source_rows": 3, "target_rows": 3, "digest_match": True}
    assert report.tables["account_operations"]["target_rows"] == 4
    assert report.invariants["target"]["total_balance"] == "1000.00"
    assert report.invariants["target"]["ok"] is True

    # The service now runs on PostgreSQL with the migrated data.
    with TestClient(create_app(_settings(target))) as client:
        src, dst, _ = sqlite_source["ids"]
        with client.app.state.db.session() as session:
            service = AccountService(session)
            assert service.get_account(src).balance == Decimal("749.50")
            assert service.get_account(dst).balance == Decimal("250.50")
        # Idempotency records survived: replaying a migrated operation is a no-op.
        replay = client.post(
            f"/internal/accounts/{src}/debit",
            json={"operation_id": "tx-legacy:debit", "amount": "250.50", "currency": "INR"},
            headers=internal_headers,
        )
        assert replay.json()["replayed"] is True
        with client.app.state.db.session() as session:
            assert AccountService(session).get_account(src).balance == Decimal("749.50")


def test_dry_run_leaves_target_empty(sqlite_source, target):
    report = _import(sqlite_source["url"], target, dry_run=True)
    assert not report.committed
    assert all(t["digest_match"] for t in report.tables.values())
    assert _import(sqlite_source["url"], target).committed  # still empty afterwards


def test_refuses_non_empty_or_unmigrated_target(sqlite_source, target, postgres_url_factory):
    _import(sqlite_source["url"], target)
    with pytest.raises(ImportRefused, match="not empty"):
        _import(sqlite_source["url"], target)
    with pytest.raises(ImportRefused, match="migration head"):
        _import(sqlite_source["url"], postgres_url_factory("unmigrated"))


def test_refuses_source_that_breaks_the_ledger(sqlite_source, target):
    with sqlite3.connect(sqlite_source["path"]) as conn:  # simulate corrupted demo data
        conn.execute(
            "UPDATE accounts SET balance = balance + 1 WHERE id = ?", (sqlite_source["ids"][0],)
        )
    with pytest.raises(ImportRefused, match="ledger"):
        _import(sqlite_source["url"], target)


def test_source_is_not_modified(sqlite_source, target):
    before = sqlite_source["path"].read_bytes()
    _import(sqlite_source["url"], target)
    assert sqlite_source["path"].read_bytes() == before


def test_verification_failure_rolls_back(sqlite_source, target, monkeypatch):
    import common.sqlite_import as importer

    original = importer._digest
    calls = {"n": 0}

    def flaky_digest(rows):
        calls["n"] += 1
        return original(rows) + ("x" if calls["n"] == 4 else "")  # 2nd table's target digest

    monkeypatch.setattr(importer, "_digest", flaky_digest)
    with pytest.raises(VerificationFailed):
        _import(sqlite_source["url"], target)
    monkeypatch.setattr(importer, "_digest", original)
    assert _import(sqlite_source["url"], target).committed  # nothing had been committed

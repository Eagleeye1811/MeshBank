"""SQLite -> PostgreSQL import for the transfer saga (needs MESHBANK_TEST_POSTGRES_URL)."""

from decimal import Decimal

from sqlalchemy import func, select

from app.config import Settings
from app.main import SERVICE_DIR
from app.models import Base, TransactionEvent
from common.migrations import upgrade_to_head
from common.sqlite_import import import_database
from tests.conftest import DST, SRC, build_client
from tests.fakes import timeout

BODY = {"source_account_id": SRC, "destination_account_id": DST, "currency": "INR"}


def _settings(url):
    return Settings(_env_file=None, app_env="test", database_url=url, recovery_interval_seconds=0)


def test_saga_state_and_idempotency_survive_migration(tmp_path, postgres_url_factory, fakes, auth):
    accounts = fakes[0]
    sqlite_url = f"sqlite:///{tmp_path}/legacy-transactions.db"
    with build_client(_settings(sqlite_url), fakes) as client:
        done = client.post(
            "/transfers",
            json={**BODY, "amount": "100.00", "idempotency_key": "legacy-key-0001"},
            headers=auth(),
        ).json()
        accounts.fail_before["credit"].append(timeout())  # leave one transfer mid-saga
        pending = client.post("/transfers", json={**BODY, "amount": "40.00"}, headers=auth()).json()
    assert done["status"] == "COMPLETED"
    assert pending["status"] == "PROCESSING" and pending["step"] == "CREDIT_PENDING"

    target = postgres_url_factory("tx_import")
    upgrade_to_head(SERVICE_DIR, target)
    report = import_database(
        metadata=Base.metadata,
        service_dir=SERVICE_DIR,
        source_url=sqlite_url,
        target_url=target,
        dry_run=False,
    )
    assert report.tables["transactions"]["target_rows"] == 2
    assert all(t["digest_match"] for t in report.tables.values())

    with build_client(_settings(target), fakes) as client:
        # Idempotency record migrated: the retry is a replay, not a new transfer.
        replay = client.post(
            "/transfers",
            json={**BODY, "amount": "100.00", "idempotency_key": "legacy-key-0001"},
            headers=auth(),
        )
        assert replay.status_code == 200 and replay.json()["id"] == done["id"]
        # The PROCESSING transfer is recovered on PostgreSQL after the "restart".
        from app.recovery import run_once

        run_once(client.app.state)
        assert client.get(f"/transactions/{pending['id']}", headers=auth()).json()["status"] == (
            "COMPLETED"
        )
        # New audit events get fresh IDs (the serial sequence was advanced).
        fresh = client.post("/transfers", json={**BODY, "amount": "1.00"}, headers=auth())
        assert fresh.status_code == 201
        with client.app.state.db.session() as session:
            ids = session.scalars(select(TransactionEvent.id)).all()
            assert len(ids) == len(set(ids))
            assert session.scalar(select(func.count()).select_from(TransactionEvent)) == len(ids)
    assert accounts.balance(DST) == Decimal("50.00") + Decimal("141.00")

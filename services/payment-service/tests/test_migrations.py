"""Alembic migrations for the payment-service database."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, pool

from app.main import SERVICE_DIR
from app.models import Base
from common.migrations import (
    SchemaNotCurrentError,
    current_revision,
    head_revision,
    prepare_database,
    upgrade_to_head,
)
from common.testing import assert_models_match_migrations


def test_migrations_create_exactly_the_model_schema(database_url_factory):
    assert_models_match_migrations(SERVICE_DIR, Base.metadata, database_url_factory("drift"))


def test_upgrade_is_repeatable_and_keeps_data(database_url_factory):
    url = database_url_factory("repeat")
    upgrade_to_head(SERVICE_DIR, url)
    upgrade_to_head(SERVICE_DIR, url)  # second run is a no-op
    assert current_revision(url) == head_revision(SERVICE_DIR)
    engine = create_engine(url, poolclass=pool.NullPool)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert set(Base.metadata.tables) <= tables


def test_check_mode_refuses_an_unmigrated_database(database_url_factory):
    url = database_url_factory("unmigrated")
    with pytest.raises(SchemaNotCurrentError):
        prepare_database(
            mode="check", service_dir=SERVICE_DIR, database_url=url, base=Base, db=None
        )
    upgrade_to_head(SERVICE_DIR, url)
    prepare_database(mode="check", service_dir=SERVICE_DIR, database_url=url, base=Base, db=None)


def test_0002_widens_transaction_id_without_losing_rows(database_url_factory):
    from alembic import command
    from sqlalchemy import MetaData, Table, text

    from common.migrations import alembic_config

    url = database_url_factory("upgrade_path")
    stamp = datetime(2026, 10, 1, tzinfo=UTC)
    command.upgrade(alembic_config(SERVICE_DIR, url), "0001")
    engine = create_engine(url, poolclass=pool.NullPool)
    row = {
        "id": "p-1",
        "customer_id": "c-1",
        "account_id": "a-1",
        "payment_type": "BILL",
        "payee_name": "Water",
        "payee_reference": "W-1",
        "amount": 12345,
        "currency": "INR",
        "status": "COMPLETED",
        "step": "DONE",
        "transaction_id": "t" * 36,
        "request_hash": "h",
        "debit_attempts": 1,
        "created_at": stamp,
        "updated_at": stamp,
    }
    payments = Table("payments", MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(payments.insert(), row)
    upgrade_to_head(SERVICE_DIR, url)
    with engine.begin() as conn:
        kept = conn.execute(
            text("SELECT amount, transaction_id FROM payments WHERE id='p-1'")
        ).one()
        assert kept == (12345, "t" * 36)
        conn.execute(text("UPDATE payments SET transaction_id=:t WHERE id='p-1'"), {"t": "x" * 64})
    engine.dispose()

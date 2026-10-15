"""Alembic migrations for the transaction-service database."""

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

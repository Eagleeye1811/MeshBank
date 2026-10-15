"""Fixtures for the end-to-end suite (see harness.py for the process manager)."""

import pytest

from common.testing import PostgresTestDatabases, postgres_admin_url
from tests.e2e.harness import SERVICES, Api, Stack


@pytest.fixture(scope="session")
def postgres_databases():
    """Creates throwaway ``meshbank_test_*`` databases when
    MESHBANK_TEST_POSTGRES_URL is set; otherwise yields None (SQLite)."""
    admin = postgres_admin_url()
    if admin is None:
        yield None
        return
    databases = PostgresTestDatabases(admin)
    try:
        yield databases
    finally:
        databases.drop_all()


@pytest.fixture(scope="session")
def stack(tmp_path_factory, postgres_databases):
    urls = None
    if postgres_databases is not None:
        urls = {name: postgres_databases.create() for name in SERVICES}
    stack = Stack(tmp_path_factory.mktemp("meshbank-e2e"), database_urls=urls)
    try:
        stack.start_all()
        yield stack
    finally:
        stack.stop_all()


@pytest.fixture(autouse=True)
def _all_services_running(stack):
    """Tests may stop services; make sure each test starts with a full stack."""
    stack.start_all()
    yield
    stack.start_all()


@pytest.fixture(scope="session")
def api(stack):
    client = Api(stack)
    yield client
    client.http.close()

"""Test helpers: per-test databases on SQLite (default) or a real PostgreSQL.

Set ``MESHBANK_TEST_POSTGRES_URL`` to an administrative PostgreSQL URL (e.g.
``postgresql://user@localhost/postgres``) to run the service suites against
PostgreSQL. Each test then gets a fresh database named ``meshbank_test_<hex>``
which is dropped afterwards. Only databases with that prefix that the fixture
itself created are ever dropped.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

from common.configuration import normalize_database_url
from common.keys import SigningKey, generate_key_pair
from common.migrations import head_revision

TEST_DB_PREFIX = "meshbank_test_"
POSTGRES_ENV = "MESHBANK_TEST_POSTGRES_URL"


def postgres_admin_url() -> str | None:
    url = os.environ.get(POSTGRES_ENV)
    return normalize_database_url(url) if url else None


class PostgresTestDatabases:
    def __init__(self, admin_url: str) -> None:
        self.admin_url = admin_url
        self.created: list[str] = []
        self._engine = create_engine(
            admin_url, poolclass=pool.NullPool, isolation_level="AUTOCOMMIT"
        )

    def create(self) -> str:
        name = f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"
        with self._engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        self.created.append(name)
        return make_url(self.admin_url).set(database=name).render_as_string(hide_password=False)

    def drop_all(self) -> None:
        with self._engine.connect() as conn:
            for name in self.created:
                if name.startswith(TEST_DB_PREFIX):  # never touch anything else
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        self.created.clear()
        self._engine.dispose()


@pytest.fixture
def database_url_factory(tmp_path) -> Iterator[Callable[[str], str]]:
    """Returns ``make(name) -> url``; SQLite under tmp_path unless PostgreSQL
    is configured via MESHBANK_TEST_POSTGRES_URL."""
    admin = postgres_admin_url()
    databases = PostgresTestDatabases(admin) if admin else None

    def make(name: str) -> str:
        if databases is None:
            return f"sqlite:///{tmp_path}/{name}.db"
        return databases.create()

    yield make
    if databases is not None:
        databases.drop_all()


@pytest.fixture
def postgres_url_factory(database_url_factory) -> Callable[[str], str]:
    """Like database_url_factory but skips the test unless PostgreSQL is set up."""
    if postgres_admin_url() is None:
        pytest.skip(f"set {POSTGRES_ENV} to run PostgreSQL integration tests")
    return database_url_factory


def assert_models_match_migrations(service_dir, metadata, database_url: str) -> None:
    """After ``alembic upgrade head`` the database must match the ORM models
    exactly, so migrations and models can never silently drift apart."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from common.migrations import current_revision, upgrade_to_head

    upgrade_to_head(service_dir, database_url)
    assert current_revision(database_url) == head_revision(service_dir)
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(context, metadata)
    finally:
        engine.dispose()
    assert diff == [], f"models and migrations differ: {diff}"


class JwtTestKeys:
    """An RS256 key pair for tests plus a second, *untrusted* key pair."""

    def __init__(self, root) -> None:
        self.signing_key: SigningKey = generate_key_pair(root / "trusted", "test-key-1", bits=2048)
        self.public_dir = str(root / "trusted" / "public")
        self.private_file = str(root / "trusted" / "private" / "test-key-1.pem")
        self.untrusted_key: SigningKey = generate_key_pair(root / "other", "test-key-1", bits=2048)

    def token(
        self,
        settings,
        subject: str,
        *,
        email: str = "c@example.com",
        ttl_seconds: int = 600,
        key: SigningKey | None = None,
        now=None,
    ) -> str:
        from common.security import create_access_token

        token, _ = create_access_token(
            settings,
            signing_key=key or self.signing_key,
            subject=subject,
            email=email,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        return token


_session_keys: JwtTestKeys | None = None
_session_key_root: Path | None = None


def pytest_configure(config) -> None:
    """Generate one RS256 key pair per test session and expose it the same way
    a deployment does: through JWT_* environment variables."""
    global _session_keys, _session_key_root
    _session_key_root = Path(tempfile.mkdtemp(prefix="meshbank-jwt-"))
    _session_keys = JwtTestKeys(_session_key_root)
    os.environ["JWT_PUBLIC_KEYS_DIR"] = _session_keys.public_dir
    os.environ["JWT_PRIVATE_KEY_FILE"] = _session_keys.private_file
    os.environ["JWT_SIGNING_KEY_ID"] = _session_keys.signing_key.kid


def pytest_unconfigure(config) -> None:
    if _session_key_root is not None:
        shutil.rmtree(_session_key_root, ignore_errors=True)


@pytest.fixture(scope="session")
def jwt_keys() -> JwtTestKeys:
    assert _session_keys is not None
    return _session_keys

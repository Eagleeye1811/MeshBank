"""Alembic integration shared by every service.

Each service owns an independent migration history (``services/<svc>/migrations``)
and its own ``alembic_version`` table in its own database. Migrations only ever
move forward at startup; nothing here drops or recreates data.

* ``run_alembic_env`` is called from each service's ``migrations/env.py``.
* ``prepare_database`` is called at application start according to
  ``DB_MIGRATION_MODE`` (``upgrade`` | ``check`` | ``create_all``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, pool, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger("meshbank.migrations")


class SchemaNotCurrentError(RuntimeError):
    pass


def alembic_config(service_dir: Path, database_url: str) -> Config:
    config = Config(str(service_dir / "alembic.ini"))
    config.set_main_option("script_location", str(service_dir / "migrations"))
    # ``%`` must be escaped for configparser interpolation.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["programmatic"] = True
    return config


def head_revision(service_dir: Path) -> str | None:
    script = ScriptDirectory.from_config(alembic_config(service_dir, "sqlite://"))
    return script.get_current_head()


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade_to_head(service_dir: Path, database_url: str) -> None:
    command.upgrade(alembic_config(service_dir, database_url), "head")


def prepare_database(
    *, mode: str, service_dir: Path, database_url: str, base: type[DeclarativeBase], db
) -> None:
    """Bring the schema up to date (``upgrade``), verify it (``check``), or use
    the legacy development shortcut (``create_all``, SQLite/dev only)."""
    if mode == "upgrade":
        upgrade_to_head(service_dir, database_url)
    elif mode == "check":
        current, head = current_revision(database_url), head_revision(service_dir)
        if current != head:
            raise SchemaNotCurrentError(
                f"Database schema revision is {current!r} but this release requires {head!r}. "
                "Run 'alembic upgrade head' for this service before starting it."
            )
    elif mode == "create_all":
        db.create_schema(base)
    else:  # pragma: no cover - guarded by settings validation
        raise ValueError(f"Unknown DB_MIGRATION_MODE {mode!r}")


def _render_item(type_, obj, autogen_context):
    """Render application TypeDecorators as their portable implementation type
    so migration files never import application code."""
    if type_ == "type" and isinstance(obj, TypeDecorator):
        impl = obj.impl if not isinstance(obj.impl, type) else obj.impl()
        autogen_context.imports.add("import sqlalchemy as sa")
        return f"sa.{impl!r}"
    return False


def _database_url_from_environment(default_url: str) -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("DATABASE_URL="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return default_url


def run_alembic_env(target_metadata: MetaData, *, service: str, default_url: str) -> None:
    """Body of every service's ``migrations/env.py``."""
    from alembic import context

    config = context.config
    if not config.attributes.get("programmatic") and not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    url = config.get_main_option("sqlalchemy.url") or _database_url_from_environment(default_url)
    is_sqlite = make_url(url).get_backend_name() == "sqlite"
    options = {
        "target_metadata": target_metadata,
        "render_as_batch": is_sqlite,
        "render_item": _render_item,
        "compare_type": True,
    }

    if context.is_offline_mode():
        context.configure(url=url, literal_binds=True, **options)
        with context.begin_transaction():
            context.run_migrations()
        return

    if is_sqlite:
        database = make_url(url).database
        if database and database != ":memory:":
            Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        _with_lock(connection, service, is_sqlite, lambda: _migrate(context, connection, options))
    engine.dispose()


def _migrate(context, connection: Connection, options: dict) -> None:
    context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


def _with_lock(connection: Connection, service: str, is_sqlite: bool, fn) -> None:
    """Serialise concurrent migrators (e.g. several replicas starting at once)
    with a PostgreSQL advisory lock. SQLite serialises writers itself."""
    if is_sqlite:
        fn()
        return
    key = {"key": f"meshbank-migrations:{service}"}
    connection.execute(text("SELECT pg_advisory_lock(hashtext(:key))"), key)
    connection.commit()
    try:
        fn()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), key)
        connection.commit()

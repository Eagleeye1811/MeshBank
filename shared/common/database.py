"""Persistence helpers: engine/session management and portable column types.

Synchronous SQLAlchemy sessions are used consistently across all services.
The helpers avoid SQLite-specific assumptions so the same models work on
PostgreSQL by changing ``DATABASE_URL``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from sqlalchemy import BigInteger, DateTime, Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from common.money import DEFAULT_CURRENCY, from_minor_units, to_minor_units

logger = logging.getLogger("meshbank.database")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC timestamps; always returns timezone-aware UTC datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use UTC-aware values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:  # SQLite drops tzinfo
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class MoneyType(TypeDecorator[Decimal]):
    """Exact monetary storage as integer minor units (paise for INR).

    ``Numeric`` is not exact on SQLite, so a BIGINT of minor units is used; it
    is equally portable to PostgreSQL. Arithmetic in SQL expressions (e.g.
    ``balance - :amount``) stays exact because both operands are integers.
    """

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect) -> int | None:
        if value is None:
            return None
        return to_minor_units(Decimal(value), DEFAULT_CURRENCY)

    def process_result_value(self, value: int | None, dialect) -> Decimal | None:
        if value is None:
            return None
        return from_minor_units(int(value), DEFAULT_CURRENCY)


def make_base() -> type[DeclarativeBase]:
    class ServiceBase(DeclarativeBase):
        pass

    return ServiceBase


def redact_database_url(url: str) -> str:
    try:
        parsed = make_url(url)
    except Exception:
        return "<invalid database url>"
    return parsed.render_as_string(hide_password=True)


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        parsed = make_url(url)
        kwargs: dict = {"echo": echo, "pool_pre_ping": True}
        if parsed.get_backend_name() == "sqlite":
            database = parsed.database
            if database and database != ":memory:":
                Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine: Engine = create_engine(url, **kwargs)
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self, base: type[DeclarativeBase]) -> None:
        """Create missing tables. Never drops or alters existing data."""
        base.metadata.create_all(self.engine)

    def drop_schema(self, base: type[DeclarativeBase]) -> None:
        base.metadata.drop_all(self.engine)

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("database ping failed", exc_info=True)
            return False

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _configure_sqlite(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the app's database."""
    db: Database = request.app.state.db
    with db.session() as session:
        yield session

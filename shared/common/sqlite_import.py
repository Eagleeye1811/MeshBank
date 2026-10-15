"""Copy one service's SQLite data into its (migrated, empty) target database.

Run from the service directory (it imports ``app.models``)::

    # target defaults to $DATABASE_URL
    python -m common.sqlite_import --source sqlite:////data/accounts.db
    python -m common.sqlite_import --source sqlite:///./data/accounts.db \
        --target postgresql://... --dry-run

Safety rules:
* the source is opened read-only and never modified;
* the target must already be at the Alembic head revision and contain no rows;
* everything is copied in ONE target transaction; before committing, row
  counts, a per-table content digest and domain invariants are verified on
  both sides. Any mismatch rolls the whole import back (target stays empty);
* ``--dry-run`` performs the full copy and verification, then rolls back.
"""

from __future__ import annotations

import argparse
import enum
import hashlib
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Integer, MetaData, Table, create_engine, func, pool, select, text
from sqlalchemy.engine import Connection, make_url

from common.configuration import normalize_database_url
from common.migrations import current_revision, head_revision

BATCH_SIZE = 500


class ImportRefused(RuntimeError):
    """Preconditions not met; nothing was written."""


class VerificationFailed(RuntimeError):
    """Copied data did not verify; the import was rolled back."""


@dataclass
class ImportReport:
    tables: dict[str, dict] = field(default_factory=dict)
    invariants: dict[str, object] = field(default_factory=dict)
    committed: bool = False

    def as_dict(self) -> dict:
        return {"tables": self.tables, "invariants": self.invariants, "committed": self.committed}


def _normalize(value: object) -> object:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rows(conn: Connection, table: Table) -> list[dict]:
    query = select(table).order_by(*table.primary_key.columns)
    return [dict(row._mapping) for row in conn.execute(query)]


def _digest(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps({k: _normalize(v) for k, v in row.items()}, sort_keys=True).encode())
    return h.hexdigest()


def _count(conn: Connection, table: Table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def ledger_invariant(conn: Connection, metadata: MetaData) -> dict | None:
    """Account balances must equal the sum of their applied operations."""
    if not {"accounts", "account_operations"} <= set(metadata.tables):
        return None
    accounts, ops = metadata.tables["accounts"], metadata.tables["account_operations"]
    totals: dict[str, Decimal] = {}
    for row in conn.execute(select(ops).where(ops.c.status == "APPLIED")):
        sign = -1 if _normalize(row.kind) == "DEBIT" else 1
        totals[row.account_id] = totals.get(row.account_id, Decimal("0")) + sign * row.amount
    mismatched, total_balance, negative = [], Decimal("0"), 0
    for row in conn.execute(select(accounts)):
        total_balance += row.balance
        negative += row.balance < 0
        if totals.get(row.id, Decimal("0")) != row.balance:
            mismatched.append(row.id)
    return {
        "accounts": _count(conn, accounts),
        "total_balance": str(total_balance),
        "negative_balances": int(negative),
        "ledger_mismatches": len(mismatched),
        "ok": not mismatched and not negative,
    }


def import_database(
    *, metadata: MetaData, service_dir: Path, source_url: str, target_url: str, dry_run: bool
) -> ImportReport:
    source_url, target_url = normalize_database_url(source_url), normalize_database_url(target_url)
    if make_url(source_url).get_backend_name() != "sqlite":
        raise ImportRefused("--source must be a SQLite URL")
    source_path = make_url(source_url).database
    if not source_path or not Path(source_path).is_file():
        raise ImportRefused("source SQLite file does not exist")
    head = head_revision(service_dir)
    if current_revision(target_url) != head:
        raise ImportRefused(f"target database is not at migration head {head!r}; migrate it first")

    # Read-only URI so the source can never be modified (WAL is still replayed).
    source = create_engine(
        f"sqlite:///file:{Path(source_path).resolve()}?mode=ro&uri=true", poolclass=pool.NullPool
    )
    target = create_engine(target_url, poolclass=pool.NullPool)
    report = ImportReport()
    try:
        with source.connect() as src, target.connect() as dst:
            existing = set(
                src.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
            )
            missing = [t.name for t in metadata.sorted_tables if t.name not in existing]
            if missing:
                raise ImportRefused(f"source is missing tables: {', '.join(missing)}")
            for table in metadata.sorted_tables:
                if _count(dst, table):
                    raise ImportRefused(f"target table {table.name!r} is not empty; refusing")

            source_invariant = ledger_invariant(src, metadata)
            if source_invariant is not None and not source_invariant["ok"]:
                raise ImportRefused(f"source fails the ledger invariant: {source_invariant}")

            dst.rollback()  # end the read-only checks before the import transaction
            trans = dst.begin()
            try:
                for table in metadata.sorted_tables:
                    rows = _rows(src, table)
                    for start in range(0, len(rows), BATCH_SIZE):
                        dst.execute(table.insert(), rows[start : start + BATCH_SIZE])
                    _reset_sequence(dst, table)
                    copied = _rows(dst, table)
                    entry = {
                        "source_rows": len(rows),
                        "target_rows": len(copied),
                        "digest_match": _digest(rows) == _digest(copied),
                    }
                    report.tables[table.name] = entry
                    if entry["source_rows"] != entry["target_rows"] or not entry["digest_match"]:
                        raise VerificationFailed(f"table {table.name!r} did not verify: {entry}")
                target_invariant = ledger_invariant(dst, metadata)
                if target_invariant is not None:
                    report.invariants = {"source": source_invariant, "target": target_invariant}
                    if target_invariant != source_invariant:
                        raise VerificationFailed("ledger invariant differs after copy")
                if dry_run:
                    trans.rollback()
                else:
                    trans.commit()
                    report.committed = True
            except Exception:
                if trans.is_active:
                    trans.rollback()
                raise
    finally:
        source.dispose()
        target.dispose()
    return report


def _reset_sequence(conn: Connection, table: Table) -> None:
    """Keep PostgreSQL serial sequences ahead of the copied IDs."""
    if conn.dialect.name != "postgresql":
        return
    for column in table.primary_key.columns:
        if isinstance(column.type, Integer) and column.autoincrement in (True, "auto"):
            highest = conn.execute(select(func.max(column))).scalar() or 0
            conn.execute(
                text("SELECT setval(pg_get_serial_sequence(:t, :c), :v, false)"),
                {"t": table.name, "c": column.name, "v": highest + 1},
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--source", required=True, help="SQLite URL of the old database")
    parser.add_argument("--target", default=None, help="target URL (default: $DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = args.target or os.environ.get("DATABASE_URL")
    if not target:
        print("No --target given and DATABASE_URL is not set", file=sys.stderr)
        return 2
    sys.path.insert(0, os.getcwd())
    models = importlib.import_module("app.models")
    service_dir = Path(models.__file__).resolve().parents[1]
    try:
        report = import_database(
            metadata=models.Base.metadata,
            service_dir=service_dir,
            source_url=args.source,
            target_url=target,
            dry_run=args.dry_run,
        )
    except (ImportRefused, VerificationFailed) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

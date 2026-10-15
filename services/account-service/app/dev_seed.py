"""DEVELOPMENT ONLY: add simulated funds to an account directly in the database.

    python -m app.dev_seed --account-id <uuid> --amount 5000.00

Refuses to run unless APP_ENV is development/test and DEV_SEED_ENABLED=true.
"""

import argparse
import sys
import uuid
from decimal import Decimal

from app.config import Settings
from app.models import Base
from common.configuration import AppEnv
from common.database import Database
from common.errors import ServiceError
from common.money import MoneyError, to_decimal, validate_positive_amount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--amount", required=True)
    parser.add_argument("--operation-id", default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.app_env == AppEnv.PRODUCTION or not settings.dev_seed_enabled:
        print(
            "Refusing to seed: requires APP_ENV!=production and DEV_SEED_ENABLED=true",
            file=sys.stderr,
        )
        return 2
    try:
        amount: Decimal = validate_positive_amount(to_decimal(args.amount))
    except MoneyError as exc:
        print(f"Invalid amount: {exc}", file=sys.stderr)
        return 2

    from app.service import AccountService

    db = Database(settings.database_url)
    db.create_schema(Base)
    try:
        with db.session() as session:
            result = AccountService(session).seed(
                args.account_id, args.operation_id or f"dev-seed-{uuid.uuid4()}", amount
            )
            account = AccountService(session).get_account(args.account_id)
            print(
                f"Seeded {result.operation.amount} {result.operation.currency} "
                f"(operation {result.operation.operation_id}, status {result.operation.status}); "
                f"balance is now {account.balance}"
            )
    except ServiceError as exc:
        print(f"Seed failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

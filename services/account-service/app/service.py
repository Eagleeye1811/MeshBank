"""Account business logic.

Balance changes are applied with a single conditional ``UPDATE`` (so
concurrent debits cannot overdraw an account) and recorded in the same
database transaction as an :class:`AccountOperation` keyed by the caller's
operation ID. Replaying an operation ID returns the original outcome; reusing
it with a different payload is a conflict.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountOperation,
    AccountStatus,
    AccountType,
    OperationKind,
    OperationStatus,
)
from common.api import PageParams, fingerprint
from common.database import utcnow
from common.errors import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger("account.service")


@dataclass(frozen=True)
class OperationResult:
    operation: AccountOperation
    replayed: bool


class AccountService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ----- customer-facing -------------------------------------------------
    def create_account(
        self, *, customer_id: str, account_type: AccountType, currency: str
    ) -> Account:
        for _ in range(5):
            account = Account(
                customer_id=customer_id,
                account_type=account_type,
                currency=currency,
                account_number=f"{secrets.randbelow(10**12):012d}",
            )
            self.session.add(account)
            try:
                self.session.commit()
            except IntegrityError:  # account-number collision; retry
                self.session.rollback()
                continue
            logger.info(
                "account created", extra={"account_id": account.id, "customer_id": customer_id}
            )
            return account
        raise ConflictError("Could not allocate an account number", code="ACCOUNT_NUMBER_EXHAUSTED")

    def get_account(self, account_id: str) -> Account:
        account = self.session.get(Account, account_id)
        if account is None:
            raise NotFoundError("Account not found", code="ACCOUNT_NOT_FOUND")
        return account

    def get_owned_account(self, account_id: str, customer_id: str) -> Account:
        account = self.get_account(account_id)
        if account.customer_id != customer_id:
            logger.warning("ownership check failed", extra={"account_id": account_id})
            raise ForbiddenError("You do not have access to this account", code="FORBIDDEN")
        return account

    def list_accounts(self, customer_id: str, page: PageParams) -> tuple[list[Account], int]:
        query = select(Account).where(Account.customer_id == customer_id)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = self.session.scalars(
            query.order_by(Account.created_at, Account.id).limit(page.limit).offset(page.offset)
        ).all()
        return list(items), total

    # ----- internal operations --------------------------------------------
    def set_status(self, account_id: str, status: AccountStatus) -> Account:
        account = self.get_account(account_id)
        account.status = status
        self.session.commit()
        logger.info("account status changed", extra={"account_id": account_id, "status": status})
        return account

    def debit(
        self,
        account_id: str,
        operation_id: str,
        amount: Decimal,
        currency: str,
        reference: str | None,
    ) -> OperationResult:
        return self._apply(
            OperationKind.DEBIT, account_id, operation_id, amount, currency, reference
        )

    def credit(
        self,
        account_id: str,
        operation_id: str,
        amount: Decimal,
        currency: str,
        reference: str | None,
    ) -> OperationResult:
        return self._apply(
            OperationKind.CREDIT, account_id, operation_id, amount, currency, reference
        )

    def seed(self, account_id: str, operation_id: str, amount: Decimal) -> OperationResult:
        account = self.get_account(account_id)
        return self._apply(
            OperationKind.DEV_SEED, account_id, operation_id, amount, account.currency, "dev-seed"
        )

    def reverse(
        self, account_id: str, operation_id: str, original_operation_id: str, reference: str | None
    ) -> OperationResult:
        """Compensate an applied debit by crediting the same amount back once."""
        request_hash = fingerprint(
            {
                "kind": "REVERSAL",
                "account_id": account_id,
                "original": original_operation_id,
                "reference": reference,
            }
        )
        replay = self._replay(operation_id, request_hash)
        if replay:
            return replay
        original = self.session.get(AccountOperation, original_operation_id)
        if original is None or original.account_id != account_id:
            raise NotFoundError("Original operation not found", code="OPERATION_NOT_FOUND")
        if original.kind != OperationKind.DEBIT:
            raise ConflictError("Only debits can be reversed", code="NOT_REVERSIBLE")
        if original.status != OperationStatus.APPLIED:
            raise ConflictError("The original debit was not applied", code="NOT_REVERSIBLE")
        already = self.session.scalar(
            select(AccountOperation.operation_id).where(
                AccountOperation.reverses_operation_id == original_operation_id
            )
        )
        if already is not None:
            raise ConflictError("This debit has already been reversed", code="ALREADY_REVERSED")
        # Reversals return money to the customer and therefore succeed even when
        # the account has since been frozen.
        self.session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(balance=Account.balance + original.amount, updated_at=utcnow())
        )
        operation = AccountOperation(
            operation_id=operation_id,
            account_id=account_id,
            kind=OperationKind.REVERSAL,
            amount=original.amount,
            currency=original.currency,
            reference=reference,
            status=OperationStatus.APPLIED,
            reverses_operation_id=original_operation_id,
            request_hash=request_hash,
        )
        return self._commit_operation(operation, request_hash)

    def get_operation(self, operation_id: str) -> AccountOperation:
        operation = self.session.get(AccountOperation, operation_id)
        if operation is None:
            raise NotFoundError("Operation not found", code="OPERATION_NOT_FOUND")
        return operation

    # ----- helpers ----------------------------------------------------------
    def _apply(
        self,
        kind: OperationKind,
        account_id: str,
        operation_id: str,
        amount: Decimal,
        currency: str,
        reference: str | None,
    ) -> OperationResult:
        request_hash = fingerprint(
            {
                "kind": kind.value,
                "account_id": account_id,
                "amount": str(amount),
                "currency": currency,
                "reference": reference,
            }
        )
        replay = self._replay(operation_id, request_hash)
        if replay:
            return replay
        account = self.get_account(account_id)

        rejection: str | None = None
        if account.currency != currency:
            rejection = "CURRENCY_MISMATCH"
        elif account.status != AccountStatus.ACTIVE:
            rejection = "ACCOUNT_INACTIVE"
        else:
            stmt = update(Account).where(
                Account.id == account_id, Account.status == AccountStatus.ACTIVE
            )
            if kind == OperationKind.DEBIT:
                stmt = stmt.where(Account.balance >= amount).values(
                    balance=Account.balance - amount, updated_at=utcnow()
                )
            else:
                stmt = stmt.values(balance=Account.balance + amount, updated_at=utcnow())
            if self.session.execute(stmt).rowcount != 1:
                rejection = (
                    "INSUFFICIENT_FUNDS" if kind == OperationKind.DEBIT else "ACCOUNT_INACTIVE"
                )

        operation = AccountOperation(
            operation_id=operation_id,
            account_id=account_id,
            kind=kind,
            amount=amount,
            currency=currency,
            reference=reference,
            status=OperationStatus.REJECTED if rejection else OperationStatus.APPLIED,
            rejection_code=rejection,
            request_hash=request_hash,
        )
        return self._commit_operation(operation, request_hash)

    def _replay(self, operation_id: str, request_hash: str) -> OperationResult | None:
        existing = self.session.get(AccountOperation, operation_id)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise ConflictError(
                "Operation ID was already used with a different request",
                code="OPERATION_ID_CONFLICT",
            )
        return OperationResult(existing, replayed=True)

    def _commit_operation(self, operation: AccountOperation, request_hash: str) -> OperationResult:
        self.session.add(operation)
        try:
            self.session.commit()
        except IntegrityError as exc:
            # A concurrent request with the same operation ID won the race; our
            # balance update is rolled back together with the insert.
            self.session.rollback()
            if operation.reverses_operation_id:
                existing = self.session.get(AccountOperation, operation.operation_id)
                if existing is None:
                    raise ConflictError(
                        "This debit has already been reversed", code="ALREADY_REVERSED"
                    ) from exc
            replay = self._replay(operation.operation_id, request_hash)
            if replay is None:
                raise
            return replay
        logger.info(
            "account operation recorded",
            extra={
                "operation_id": operation.operation_id,
                "account_id": operation.account_id,
                "kind": operation.kind.value,
                "status": operation.status.value,
                "rejection_code": operation.rejection_code,
            },
        )
        return OperationResult(operation, replayed=False)

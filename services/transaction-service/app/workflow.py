"""Durable transfer workflow (a small, explicit saga).

The Account Service owns balances, so no database transaction spans both
services. Instead every transfer is driven through persisted steps::

    CREATED -> FRAUD_CHECK -> DEBIT_PENDING -> CREDIT_PENDING -> DONE
                                     \\                \\
                                      \\                -> REVERSAL_PENDING -> DONE (FAILED)
                                       -> DONE (REJECTED / FAILED)

* The step is committed *before* each remote call, so after a crash the
  workflow resumes from the last step it might have started.
* Every account call uses a deterministic operation ID (``<tx>:debit``,
  ``<tx>:credit``, ``<tx>:reversal``) and the Account Service applies each
  operation at most once, so resuming simply re-sends the same call.
* An unknown outcome (timeout, outage) never marks a transfer COMPLETED or
  FAILED; it stays PROCESSING and the recovery loop rolls it forward.
* If the credit is definitively rejected, the debit is compensated by an
  idempotent reversal and the transfer ends FAILED.

This gives at-most-once balance effects and eventual completion or
compensation. It does NOT provide distributed ACID isolation: between the
debit and the credit, the money is visible in neither account.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import AccountGateway, FraudGateway, NotificationGateway, OperationOutcome
from app.models import (
    NotificationState,
    Transaction,
    TransactionEvent,
    TransactionKind,
    TransactionStatus,
    WorkflowStep,
)
from app.schemas import PaymentDebitRequest, RecoveryReport, TransferRequest
from app.state import FINAL_STATUSES, check_transition
from common.api import fingerprint
from common.database import utcnow
from common.errors import (
    BadRequestError,
    ConflictError,
    DownstreamError,
    ForbiddenError,
    NotFoundError,
    ServiceError,
)

logger = logging.getLogger("transaction.workflow")

S = TransactionStatus
UNAVAILABLE_FAILURES = {"FRAUD_SERVICE_UNAVAILABLE", "ACCOUNT_SERVICE_UNAVAILABLE"}


def mask(account_id: str | None) -> str:
    return f"****{account_id[-4:]}" if account_id else "****"


class TransferWorkflow:
    def __init__(
        self,
        session: Session,
        *,
        accounts: AccountGateway,
        fraud: FraudGateway,
        notifications: NotificationGateway,
        lease_seconds: int = 60,
    ) -> None:
        self.session = session
        self.accounts = accounts
        self.fraud = fraud
        self.notifications = notifications
        self.lease_seconds = lease_seconds

    # ------------------------------------------------------------------ entry
    def submit_transfer(self, customer_id: str, req: TransferRequest) -> tuple[Transaction, bool]:
        """Returns (transaction, created). ``created`` is False for an
        idempotent replay of an earlier request."""
        request_hash = fingerprint(
            {
                "kind": "TRANSFER",
                "source": req.source_account_id,
                "destination": req.destination_account_id,
                "amount": str(req.amount),
                "currency": req.currency,
                "description": req.description,
            }
        )
        if req.idempotency_key:
            existing = self._find_idempotent(customer_id, req.idempotency_key, request_hash)
            if existing:
                return existing, False
        if req.source_account_id == req.destination_account_id:
            raise BadRequestError(
                "Source and destination accounts must differ", code="SAME_ACCOUNT"
            )
        self._validate_account(
            req.source_account_id, req.currency, owner=customer_id, role="SOURCE"
        )
        self._validate_account(
            req.destination_account_id, req.currency, owner=None, role="DESTINATION"
        )

        tx = Transaction(
            kind=TransactionKind.TRANSFER,
            customer_id=customer_id,
            source_account_id=req.source_account_id,
            destination_account_id=req.destination_account_id,
            amount=req.amount,
            currency=req.currency,
            description=req.description,
            status=S.PENDING,
            step=WorkflowStep.CREATED,
            idempotency_key=req.idempotency_key,
            request_hash=request_hash,
            notification_status=NotificationState.NOT_REQUIRED,
            lease_until=utcnow() + timedelta(seconds=self.lease_seconds),
        )
        created = self._insert(tx, customer_id, request_hash)
        if created is not tx:
            return created, False
        self.run(tx)
        return tx, True

    def submit_payment_debit(self, req: PaymentDebitRequest) -> tuple[Transaction, bool]:
        """Debit for a payment. Fraud evaluation is performed by the payment
        service before calling, so this workflow starts at DEBIT_PENDING."""
        key = f"payment:{req.payment_id}"
        request_hash = fingerprint(
            {
                "kind": "PAYMENT_DEBIT",
                "account": req.account_id,
                "amount": str(req.amount),
                "currency": req.currency,
                "payee": req.payee,
            }
        )
        existing = self._find_idempotent(req.customer_id, key, request_hash)
        if existing:
            if existing.status not in FINAL_STATUSES and self._claim(existing.id):
                self.run(existing)  # retry from payment-service resumes the workflow
            return existing, False
        self._validate_account(req.account_id, req.currency, owner=req.customer_id, role="SOURCE")
        tx = Transaction(
            kind=TransactionKind.PAYMENT_DEBIT,
            customer_id=req.customer_id,
            source_account_id=req.account_id,
            payee=req.payee,
            amount=req.amount,
            currency=req.currency,
            description=f"Payment to {req.payee}"[:140],
            status=S.PROCESSING,
            step=WorkflowStep.DEBIT_PENDING,
            fraud_decision="EVALUATED_BY_CALLER",
            idempotency_key=key,
            request_hash=request_hash,
            # The payment service notifies the customer about payments.
            notification_status=NotificationState.NOT_REQUIRED,
            lease_until=utcnow() + timedelta(seconds=self.lease_seconds),
        )
        created = self._insert(tx, req.customer_id, request_hash)
        if created is not tx:
            return created, False
        self.run(tx)
        return tx, True

    def reverse_payment_debit(self, tx: Transaction, reason: str) -> Transaction:
        """Refund a completed payment debit (e.g. the provider declined)."""
        if tx.kind != TransactionKind.PAYMENT_DEBIT:
            raise ConflictError("Only payment debits can be reversed", code="NOT_REVERSIBLE")
        if tx.status == S.REVERSED:
            return tx
        if tx.status != S.COMPLETED:
            raise ConflictError("Only completed debits can be reversed", code="NOT_REVERSIBLE")
        if tx.step != WorkflowStep.REVERSAL_PENDING:
            tx.step = WorkflowStep.REVERSAL_PENDING
            tx.failure_reason = reason[:255]
            self._event(tx, tx.status, "reversal requested")
            self.session.commit()
        if self._claim(tx.id):
            self.run(tx)
        return tx

    # ------------------------------------------------------------- execution
    def run(self, tx: Transaction) -> Transaction:
        """Advance the workflow as far as possible from its persisted step.

        Callers must hold the transaction's lease."""
        try:
            while tx.step != WorkflowStep.DONE:
                if not self._advance(tx):
                    break  # outcome unknown; leave for recovery
        finally:
            tx.lease_until = None
            self.session.commit()
        if tx.status in FINAL_STATUSES and tx.notification_status in (
            NotificationState.PENDING,
            NotificationState.FAILED,
        ):
            self.send_notification(tx)
        return tx

    def _advance(self, tx: Transaction) -> bool:
        step = tx.step
        if step == WorkflowStep.CREATED:
            self._set(tx, S.PROCESSING, WorkflowStep.FRAUD_CHECK, "processing started")
            return True
        if step == WorkflowStep.FRAUD_CHECK:
            return self._fraud_check(tx)
        if step == WorkflowStep.DEBIT_PENDING:
            return self._debit(tx)
        if step == WorkflowStep.CREDIT_PENDING:
            return self._credit(tx)
        if step == WorkflowStep.REVERSAL_PENDING:
            return self._reverse(tx)
        raise RuntimeError(f"unexpected step {step}")

    def _fraud_check(self, tx: Transaction) -> bool:
        try:
            result = self.fraud.evaluate(
                {
                    "operation_id": tx.id,
                    "operation_type": "TRANSFER",
                    "customer_id": tx.customer_id,
                    "source_account_id": tx.source_account_id,
                    "destination_account_id": tx.destination_account_id,
                    "amount": str(tx.amount),
                    "currency": tx.currency,
                }
            )
        except DownstreamError:
            # Fail closed: no money has moved, so FAILED is a safe final state.
            self._finish(
                tx,
                S.FAILED,
                "FRAUD_SERVICE_UNAVAILABLE",
                "Fraud evaluation unavailable; no funds were moved",
            )
            return True
        tx.fraud_decision = result.decision
        tx.fraud_reason_codes = ",".join(result.reason_codes)
        if result.decision == "REJECT":
            self._finish(tx, S.REJECTED, "FRAUD_REJECTED", result.explanation)
        elif result.decision == "REVIEW":
            self._finish(tx, S.UNDER_REVIEW, "FRAUD_REVIEW", result.explanation)
        elif result.decision == "APPROVE":
            self._set(tx, S.PROCESSING, WorkflowStep.DEBIT_PENDING, "fraud approved")
        else:
            self._finish(tx, S.FAILED, "FRAUD_DECISION_INVALID", "Unknown fraud decision")
        return True

    def _debit(self, tx: Transaction) -> bool:
        first_attempt = tx.debit_attempts == 0
        tx.debit_attempts += 1
        self.session.commit()  # durable before the call: later attempts know a debit may exist
        outcome = self._call(
            tx,
            lambda: self.accounts.debit(
                tx.source_account_id, tx.debit_operation_id, tx.amount, tx.currency, tx.id
            ),
            definitely_not_applied_ok=first_attempt,
        )
        if outcome is None:
            return tx.step == WorkflowStep.DONE
        if outcome.applied:
            if tx.kind == TransactionKind.PAYMENT_DEBIT:
                self._finish(tx, S.COMPLETED, None, None)
            else:
                self._set(tx, S.PROCESSING, WorkflowStep.CREDIT_PENDING, "source debited")
        else:
            code = outcome.rejection_code or "DEBIT_REJECTED"
            self._finish(tx, S.REJECTED, code, _REJECTION_MESSAGES.get(code, "Debit rejected"))
        return True

    def _credit(self, tx: Transaction) -> bool:
        if tx.destination_account_id is None:
            raise RuntimeError(f"transfer {tx.id} has no destination account")
        outcome = self._call(
            tx,
            lambda: self.accounts.credit(
                tx.destination_account_id, tx.credit_operation_id, tx.amount, tx.currency, tx.id
            ),
        )
        if outcome is None:
            return False
        if outcome.applied:
            self._finish(tx, S.COMPLETED, None, None)
        else:
            tx.failure_code = f"CREDIT_REJECTED_{outcome.rejection_code or 'UNKNOWN'}"[:64]
            tx.failure_reason = "Destination could not be credited; reversing the debit"
            self._set(tx, S.PROCESSING, WorkflowStep.REVERSAL_PENDING, tx.failure_code)
        return True

    def _reverse(self, tx: Transaction) -> bool:
        outcome = self._call(
            tx,
            lambda: self.accounts.reverse(
                tx.source_account_id, tx.reversal_operation_id, tx.debit_operation_id, tx.id
            ),
        )
        if outcome is None:
            return False
        if not outcome.applied:  # reversals are never business-rejected
            tx.failure_reason = "Reversal was not applied; operator action required"
            self.session.commit()
            logger.error("reversal not applied", extra={"transaction_id": tx.id})
            return False
        if tx.kind == TransactionKind.PAYMENT_DEBIT:
            self._finish(tx, S.REVERSED, "PAYMENT_REVERSED", "Payment debit reversed")
        else:
            self._finish(
                tx,
                S.FAILED,
                tx.failure_code or "CREDIT_REJECTED",
                "Destination could not be credited; the debit was reversed",
            )
        return True

    def _call(
        self, tx: Transaction, fn, *, definitely_not_applied_ok: bool = False
    ) -> OperationOutcome | None:
        """Run an account operation. Returns None when the outcome is unknown
        (the transfer stays PROCESSING for recovery) or when the transfer was
        finished as FAILED because the call provably never happened."""
        try:
            return fn()
        except DownstreamError as exc:
            if definitely_not_applied_ok and not exc.maybe_processed:
                self._finish(
                    tx,
                    S.FAILED,
                    "ACCOUNT_SERVICE_UNAVAILABLE",
                    "Account service unavailable; no funds were moved",
                )
                return None
            tx.failure_reason = f"Awaiting recovery: {exc.code}"[:255]
            self.session.commit()
            logger.warning(
                "transfer step outcome unknown; will retry",
                extra={"transaction_id": tx.id, "step": tx.step.value, "cause": exc.code},
            )
            return None
        except ServiceError as exc:
            # Unexpected contract violation (e.g. operation ID conflict). Keep
            # the transfer recoverable and visible rather than guessing.
            tx.failure_reason = f"Operator attention required: {exc.code}"[:255]
            self.session.commit()
            logger.error(
                "transfer step failed unexpectedly",
                extra={"transaction_id": tx.id, "step": tx.step.value, "cause": exc.code},
            )
            return None

    # ------------------------------------------------------------- recovery
    def recover(self, limit: int = 100) -> RecoveryReport:
        """Resume unfinished workflows and resend undelivered notifications.
        Safe to run concurrently with requests and with other recoverers."""
        candidates = self.session.scalars(
            select(Transaction)
            .where(
                or_(
                    Transaction.step != WorkflowStep.DONE,
                    Transaction.notification_status.in_(
                        [NotificationState.PENDING, NotificationState.FAILED]
                    ),
                )
            )
            .order_by(Transaction.created_at)
            .limit(limit)
        ).all()
        advanced = sent = 0
        for tx in candidates:
            if not self._claim(tx.id):
                continue
            self.session.refresh(tx)
            before = (tx.status, tx.step)
            if tx.step != WorkflowStep.DONE:
                tx.failure_reason = None if tx.status == S.PROCESSING else tx.failure_reason
                self.run(tx)
            else:
                tx.lease_until = None
                self.session.commit()
                self.send_notification(tx)
            if (tx.status, tx.step) != before:
                advanced += 1
            if tx.notification_status == NotificationState.SENT:
                sent += 1
        if candidates:
            logger.info("recovery pass", extra={"examined": len(candidates), "advanced": advanced})
        return RecoveryReport(examined=len(candidates), advanced=advanced, notifications_sent=sent)

    # ---------------------------------------------------------- notification
    def send_notification(self, tx: Transaction) -> None:
        if tx.notification_status in (NotificationState.NOT_REQUIRED, NotificationState.SENT):
            return
        ntype, message = _notification_for(tx)
        try:
            self.notifications.notify(
                event_key=f"transaction:{tx.id}:{tx.status.value}",
                customer_id=tx.customer_id,
                type=ntype,
                message=message,
            )
            tx.notification_status = NotificationState.SENT
        except ServiceError:
            tx.notification_status = NotificationState.FAILED
            logger.warning(
                "notification not delivered; will retry", extra={"transaction_id": tx.id}
            )
        self.session.commit()

    # --------------------------------------------------------------- helpers
    def _validate_account(self, account_id: str, currency: str, *, owner: str | None, role: str):
        try:
            account = self.accounts.get_account(account_id)
        except DownstreamError as exc:
            raise DownstreamError(
                "The account service is unavailable; please retry later",
                code="ACCOUNT_SERVICE_UNAVAILABLE",
                status_code=503,
            ) from exc
        if account is None:
            raise NotFoundError(
                f"{role.title()} account not found", code=f"{role}_ACCOUNT_NOT_FOUND"
            )
        if owner is not None and account.customer_id != owner:
            raise ForbiddenError("You do not own the source account", code="FORBIDDEN")
        if account.status != "ACTIVE":
            raise ConflictError(f"{role.title()} account is not active", code="ACCOUNT_INACTIVE")
        if account.currency != currency:
            raise BadRequestError(
                f"{role.title()} account currency does not match", code="CURRENCY_MISMATCH"
            )

    def _find_idempotent(self, customer_id: str, key: str, request_hash: str) -> Transaction | None:
        existing = self.session.scalar(
            select(Transaction).where(
                Transaction.customer_id == customer_id, Transaction.idempotency_key == key
            )
        )
        if existing is not None and existing.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was already used with a different request",
                code="IDEMPOTENCY_KEY_REUSED",
            )
        return existing

    def _insert(self, tx: Transaction, customer_id: str, request_hash: str) -> Transaction:
        self.session.add(tx)
        self.session.flush()
        self._event(tx, None, "created")
        try:
            self.session.commit()
        except IntegrityError:
            # A concurrent request with the same idempotency key won.
            self.session.rollback()
            existing = self._find_idempotent(customer_id, tx.idempotency_key or "", request_hash)
            if existing is None:
                raise
            return existing
        logger.info("transaction created", extra={"transaction_id": tx.id, "kind": tx.kind.value})
        return tx

    def _claim(self, tx_id: str) -> bool:
        now = utcnow()
        result = self.session.execute(
            update(Transaction)
            .where(
                Transaction.id == tx_id,
                or_(Transaction.lease_until.is_(None), Transaction.lease_until < now),
            )
            .values(lease_until=now + timedelta(seconds=self.lease_seconds))
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
        return result.rowcount == 1

    def _set(self, tx: Transaction, status: TransactionStatus, step: WorkflowStep, note: str):
        previous = tx.status
        check_transition(previous, status)
        tx.status = status
        tx.step = step
        self._event(tx, previous, note)
        self.session.commit()

    def _finish(self, tx, status: TransactionStatus, code: str | None, reason: str | None):
        previous = tx.status
        check_transition(previous, status)
        tx.status = status
        tx.step = WorkflowStep.DONE
        tx.failure_code = code
        tx.failure_reason = reason[:255] if reason else None
        tx.completed_at = utcnow()
        if tx.kind == TransactionKind.TRANSFER:
            tx.notification_status = NotificationState.PENDING
        self._event(tx, previous, code or "completed")
        self.session.commit()
        logger.info(
            "transaction finished",
            extra={"transaction_id": tx.id, "status": status.value, "failure_code": code},
        )

    def _event(self, tx: Transaction, previous: TransactionStatus | None, note: str) -> None:
        self.session.add(
            TransactionEvent(
                transaction_id=tx.id,
                from_status=previous.value if previous else None,
                to_status=tx.status.value,
                step=tx.step.value,
                note=note[:255],
            )
        )


_REJECTION_MESSAGES = {
    "INSUFFICIENT_FUNDS": "The source account has insufficient funds",
    "ACCOUNT_INACTIVE": "The source account is not active",
    "CURRENCY_MISMATCH": "The source account currency does not match",
    "ACCOUNT_NOT_FOUND": "The source account no longer exists",
}


def _notification_for(tx: Transaction) -> tuple[str, str]:
    amount = f"{tx.currency} {tx.amount}"
    route = f"from account {mask(tx.source_account_id)} to {mask(tx.destination_account_id)}"
    if tx.status == S.COMPLETED:
        return "TRANSFER_SUCCEEDED", f"Your transfer of {amount} {route} completed."
    if tx.status == S.UNDER_REVIEW:
        return "FRAUD_REVIEW", f"Your transfer of {amount} {route} is held for review."
    return "TRANSFER_FAILED", (
        f"Your transfer of {amount} {route} did not complete ({tx.failure_code}). "
        "No funds were moved."
    )

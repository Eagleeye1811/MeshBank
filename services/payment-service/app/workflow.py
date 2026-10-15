"""Durable payment workflow.

    CREATED -> FRAUD_CHECK -> DEBIT_PENDING -> PROVIDER_PENDING -> DONE (COMPLETED)
                                                      \\
                                                       -> REFUND_PENDING -> DONE (FAILED, refunded)

Balances are never changed here: the debit and any refund go through the
Transaction Service's idempotent payment-debit workflow (keyed by payment ID),
which in turn uses the Account Service's idempotent operations. Each step is
committed before the remote call, unknown outcomes leave the payment
PROCESSING for recovery, and the simulated provider is deterministic.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients import AccountGateway, FraudGateway, NotificationGateway, TransactionGateway
from app.models import NotificationState, Payment, PaymentStatus, PaymentStep
from app.provider import SimulatedProvider
from app.schemas import PaymentRequest, RecoveryReport
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

logger = logging.getLogger("payment.workflow")
S = PaymentStatus
FINAL = {S.COMPLETED, S.REJECTED, S.FAILED, S.UNDER_REVIEW}
UNAVAILABLE_FAILURES = {"FRAUD_SERVICE_UNAVAILABLE", "TRANSACTION_SERVICE_UNAVAILABLE"}


class PaymentWorkflow:
    def __init__(
        self,
        session: Session,
        *,
        accounts: AccountGateway,
        fraud: FraudGateway,
        transactions: TransactionGateway,
        notifications: NotificationGateway,
        provider: SimulatedProvider,
        lease_seconds: int = 60,
    ) -> None:
        self.session = session
        self.accounts = accounts
        self.fraud = fraud
        self.transactions = transactions
        self.notifications = notifications
        self.provider = provider
        self.lease_seconds = lease_seconds

    def submit(self, customer_id: str, req: PaymentRequest) -> tuple[Payment, bool]:
        request_hash = fingerprint(
            {
                "account": req.account_id,
                "type": req.payment_type.value,
                "payee": req.payee_name,
                "payee_reference": req.payee_reference,
                "amount": str(req.amount),
                "currency": req.currency,
            }
        )
        if req.idempotency_key:
            existing = self._find(customer_id, req.idempotency_key, request_hash)
            if existing:
                return existing, False
        self._validate_account(req.account_id, req.currency, customer_id)
        payment = Payment(
            customer_id=customer_id,
            account_id=req.account_id,
            payment_type=req.payment_type,
            payee_name=req.payee_name,
            payee_reference=req.payee_reference,
            amount=req.amount,
            currency=req.currency,
            status=S.PENDING,
            step=PaymentStep.CREATED,
            idempotency_key=req.idempotency_key,
            request_hash=request_hash,
            lease_until=utcnow() + timedelta(seconds=self.lease_seconds),
        )
        self.session.add(payment)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self._find(customer_id, req.idempotency_key or "", request_hash)
            if existing is None:
                raise
            return existing, False
        logger.info("payment created", extra={"payment_id": payment.id})
        self.run(payment)
        return payment, True

    # ------------------------------------------------------------- execution
    def run(self, payment: Payment) -> Payment:
        try:
            while payment.step != PaymentStep.DONE:
                if not self._advance(payment):
                    break
        finally:
            payment.lease_until = None
            self.session.commit()
        if payment.status in FINAL and payment.notification_status in (
            NotificationState.PENDING,
            NotificationState.FAILED,
        ):
            self.send_notification(payment)
        return payment

    def _advance(self, p: Payment) -> bool:
        if p.step == PaymentStep.CREATED:
            self._set(p, S.PROCESSING, PaymentStep.FRAUD_CHECK)
            return True
        if p.step == PaymentStep.FRAUD_CHECK:
            return self._fraud_check(p)
        if p.step == PaymentStep.DEBIT_PENDING:
            return self._debit(p)
        if p.step == PaymentStep.PROVIDER_PENDING:
            return self._provider(p)
        if p.step == PaymentStep.REFUND_PENDING:
            return self._refund(p)
        raise RuntimeError(f"unexpected step {p.step}")

    def _fraud_check(self, p: Payment) -> bool:
        try:
            result = self.fraud.evaluate(
                {
                    "operation_id": p.id,
                    "operation_type": "PAYMENT",
                    "customer_id": p.customer_id,
                    "source_account_id": p.account_id,
                    "payee": p.payee_reference,
                    "amount": str(p.amount),
                    "currency": p.currency,
                }
            )
        except DownstreamError:
            self._finish(
                p,
                S.FAILED,
                "FRAUD_SERVICE_UNAVAILABLE",
                "Fraud evaluation unavailable; you were not charged",
            )
            return True
        p.fraud_decision = result.decision
        p.fraud_reason_codes = ",".join(result.reason_codes)
        if result.decision == "APPROVE":
            self._set(p, S.PROCESSING, PaymentStep.DEBIT_PENDING)
        elif result.decision == "REVIEW":
            self._finish(p, S.UNDER_REVIEW, "FRAUD_REVIEW", result.explanation)
        else:
            self._finish(p, S.REJECTED, "FRAUD_REJECTED", result.explanation)
        return True

    def _debit(self, p: Payment) -> bool:
        first_attempt = p.debit_attempts == 0
        p.debit_attempts += 1
        self.session.commit()
        try:
            tx = self.transactions.payment_debit(
                payment_id=p.id,
                customer_id=p.customer_id,
                account_id=p.account_id,
                amount=p.amount,
                currency=p.currency,
                payee=p.payee_name,
            )
        except DownstreamError as exc:
            if first_attempt and not exc.maybe_processed:
                self._finish(
                    p,
                    S.FAILED,
                    "TRANSACTION_SERVICE_UNAVAILABLE",
                    "Transaction service unavailable; you were not charged",
                )
                return True
            return self._defer(p, exc.code)
        except ServiceError as exc:  # refused before any money moved (4xx)
            self._finish(p, S.REJECTED, exc.code, exc.message)
            return True
        p.transaction_id = tx.id
        if tx.status == "COMPLETED":
            self._set(p, S.PROCESSING, PaymentStep.PROVIDER_PENDING)
        elif tx.status in ("REJECTED", "FAILED"):
            self._finish(
                p,
                S.REJECTED if tx.status == "REJECTED" else S.FAILED,
                tx.failure_code or "DEBIT_FAILED",
                tx.failure_reason,
            )
        else:  # PROCESSING: debit outcome not yet known
            return self._defer(p, "DEBIT_IN_PROGRESS")
        return True

    def _provider(self, p: Payment) -> bool:
        result = self.provider.authorize(
            payment_id=p.id, payee_reference=p.payee_reference, amount=p.amount
        )
        p.provider_status = result.status
        p.provider_reference = result.reference
        if result.approved:
            self._finish(p, S.COMPLETED, None, None)
        else:
            p.failure_code = "PROVIDER_DECLINED"
            p.failure_reason = result.reason
            self._set(p, S.PROCESSING, PaymentStep.REFUND_PENDING)
        return True

    def _refund(self, p: Payment) -> bool:
        if p.transaction_id is None:
            raise RuntimeError(f"payment {p.id} has no debit transaction to refund")
        try:
            tx = self.transactions.reverse(p.transaction_id, "Declined by simulated provider")
        except ServiceError as exc:
            return self._defer(p, exc.code)
        if tx.status != "REVERSED":
            return self._defer(p, "REFUND_IN_PROGRESS")
        self._finish(
            p,
            S.FAILED,
            "PROVIDER_DECLINED",
            "Declined by the simulated provider; your account was refunded",
        )
        return True

    def _defer(self, p: Payment, cause: str) -> bool:
        p.failure_reason = f"Awaiting recovery: {cause}"[:255]
        self.session.commit()
        logger.warning(
            "payment step outcome unknown; will retry",
            extra={"payment_id": p.id, "step": p.step.value, "cause": cause},
        )
        return False

    # ------------------------------------------------------------- recovery
    def recover(self, limit: int = 100) -> RecoveryReport:
        candidates = self.session.scalars(
            select(Payment)
            .where(
                or_(
                    Payment.step != PaymentStep.DONE,
                    Payment.notification_status.in_(
                        [NotificationState.PENDING, NotificationState.FAILED]
                    ),
                )
            )
            .order_by(Payment.created_at)
            .limit(limit)
        ).all()
        advanced = sent = 0
        for p in candidates:
            if not self._claim(p.id):
                continue
            self.session.refresh(p)
            before = (p.status, p.step)
            if p.step != PaymentStep.DONE:
                self.run(p)
            else:
                p.lease_until = None
                self.session.commit()
                self.send_notification(p)
            advanced += (p.status, p.step) != before
            sent += p.notification_status == NotificationState.SENT
        return RecoveryReport(examined=len(candidates), advanced=advanced, notifications_sent=sent)

    def send_notification(self, p: Payment) -> None:
        if p.notification_status not in (NotificationState.PENDING, NotificationState.FAILED):
            return
        amount = f"{p.currency} {p.amount}"
        if p.status == S.COMPLETED:
            ntype, msg = (
                "PAYMENT_SUCCEEDED",
                f"Your payment of {amount} to {p.payee_name} completed.",
            )
        elif p.status == S.UNDER_REVIEW:
            ntype, msg = (
                "FRAUD_REVIEW",
                f"Your payment of {amount} to {p.payee_name} is held for review.",
            )
        else:
            ntype = "PAYMENT_FAILED"
            msg = f"Your payment of {amount} to {p.payee_name} did not complete ({p.failure_code})."
        try:
            self.notifications.notify(
                event_key=f"payment:{p.id}:{p.status.value}",
                customer_id=p.customer_id,
                type=ntype,
                message=msg,
            )
            p.notification_status = NotificationState.SENT
        except ServiceError:
            p.notification_status = NotificationState.FAILED
            logger.warning("notification not delivered; will retry", extra={"payment_id": p.id})
        self.session.commit()

    # --------------------------------------------------------------- helpers
    def _validate_account(self, account_id: str, currency: str, customer_id: str) -> None:
        try:
            account = self.accounts.get_account(account_id)
        except DownstreamError as exc:
            raise DownstreamError(
                "The account service is unavailable; please retry later",
                code="ACCOUNT_SERVICE_UNAVAILABLE",
                status_code=503,
            ) from exc
        if account is None:
            raise NotFoundError("Account not found", code="ACCOUNT_NOT_FOUND")
        if account.customer_id != customer_id:
            raise ForbiddenError("You do not own this account", code="FORBIDDEN")
        if account.status != "ACTIVE":
            raise ConflictError("Account is not active", code="ACCOUNT_INACTIVE")
        if account.currency != currency:
            raise BadRequestError("Account currency does not match", code="CURRENCY_MISMATCH")

    def _find(self, customer_id: str, key: str, request_hash: str) -> Payment | None:
        existing = self.session.scalar(
            select(Payment).where(
                Payment.customer_id == customer_id, Payment.idempotency_key == key
            )
        )
        if existing is not None and existing.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was already used with a different request",
                code="IDEMPOTENCY_KEY_REUSED",
            )
        return existing

    def _claim(self, payment_id: str) -> bool:
        now = utcnow()
        result = self.session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                or_(Payment.lease_until.is_(None), Payment.lease_until < now),
            )
            .values(lease_until=now + timedelta(seconds=self.lease_seconds))
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
        return result.rowcount == 1

    def _set(self, p: Payment, status: PaymentStatus, step: PaymentStep) -> None:
        if p.status in FINAL:
            raise RuntimeError(f"payment {p.id} is already final")
        p.status = status
        p.step = step
        self.session.commit()

    def _finish(
        self, p: Payment, status: PaymentStatus, code: str | None, reason: str | None
    ) -> None:
        if p.status in FINAL:
            raise RuntimeError(f"payment {p.id} is already final")
        p.status = status
        p.step = PaymentStep.DONE
        p.failure_code = code
        p.failure_reason = reason[:255] if reason else None
        p.completed_at = utcnow()
        p.notification_status = NotificationState.PENDING
        self.session.commit()
        logger.info(
            "payment finished",
            extra={"payment_id": p.id, "status": status.value, "failure_code": code},
        )

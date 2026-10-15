import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import MoneyType, UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class TransactionKind(enum.StrEnum):
    TRANSFER = "TRANSFER"  # account -> account, customer initiated
    PAYMENT_DEBIT = "PAYMENT_DEBIT"  # account -> external payee, via payment-service


class TransactionStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"  # business rejection; no money moved
    FAILED = "FAILED"  # technical failure or compensated; no net money moved
    UNDER_REVIEW = "UNDER_REVIEW"  # held by fraud rules; no money moved
    REVERSED = "REVERSED"  # a completed payment debit that was refunded


class WorkflowStep(enum.StrEnum):
    CREATED = "CREATED"
    FRAUD_CHECK = "FRAUD_CHECK"
    DEBIT_PENDING = "DEBIT_PENDING"
    CREDIT_PENDING = "CREDIT_PENDING"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    DONE = "DONE"


class NotificationState(enum.StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, validate_strings=True, length=24)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key", name="uq_transactions_idempotency"),
        Index("ix_transactions_customer_created", "customer_id", "created_at"),
        Index("ix_transactions_recovery", "step", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[TransactionKind] = mapped_column(_enum(TransactionKind, "transaction_kind"))
    customer_id: Mapped[str] = mapped_column(String(64))
    source_account_id: Mapped[str] = mapped_column(String(64))
    destination_account_id: Mapped[str | None] = mapped_column(String(64))
    payee: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str | None] = mapped_column(String(140))

    status: Mapped[TransactionStatus] = mapped_column(
        _enum(TransactionStatus, "transaction_status")
    )
    step: Mapped[WorkflowStep] = mapped_column(_enum(WorkflowStep, "workflow_step"))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    fraud_decision: Mapped[str | None] = mapped_column(String(32))
    fraud_reason_codes: Mapped[str | None] = mapped_column(Text)

    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    debit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notification_status: Mapped[NotificationState] = mapped_column(
        _enum(NotificationState, "notification_state"), default=NotificationState.NOT_REQUIRED
    )
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Deterministic operation IDs make every account call idempotent.
    @property
    def debit_operation_id(self) -> str:
        return f"{self.id}:debit"

    @property
    def credit_operation_id(self) -> str:
        return f"{self.id}:credit"

    @property
    def reversal_operation_id(self) -> str:
        return f"{self.id}:reversal"


class TransactionEvent(Base):
    """Append-only audit trail of state transitions."""

    __tablename__ = "transaction_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    step: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

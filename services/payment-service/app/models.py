import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import MoneyType, UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class PaymentType(enum.StrEnum):
    BILL = "BILL"
    MERCHANT = "MERCHANT"


class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNDER_REVIEW = "UNDER_REVIEW"


class PaymentStep(enum.StrEnum):
    CREATED = "CREATED"
    FRAUD_CHECK = "FRAUD_CHECK"
    DEBIT_PENDING = "DEBIT_PENDING"
    PROVIDER_PENDING = "PROVIDER_PENDING"
    REFUND_PENDING = "REFUND_PENDING"
    DONE = "DONE"


class NotificationState(enum.StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, validate_strings=True, length=24)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key", name="uq_payments_idempotency"),
        Index("ix_payments_customer_created", "customer_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(64))
    payment_type: Mapped[PaymentType] = mapped_column(_enum(PaymentType, "payment_type"))
    payee_name: Mapped[str] = mapped_column(String(128))
    payee_reference: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    currency: Mapped[str] = mapped_column(String(3))

    status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus, "payment_status"))
    step: Mapped[PaymentStep] = mapped_column(_enum(PaymentStep, "payment_step"))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    fraud_decision: Mapped[str | None] = mapped_column(String(32))
    fraud_reason_codes: Mapped[str | None] = mapped_column(Text)
    # Opaque ID owned by the transaction service; not tied to its format.
    transaction_id: Mapped[str | None] = mapped_column(String(64))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    provider_reference: Mapped[str | None] = mapped_column(String(64))

    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    debit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    notification_status: Mapped[NotificationState | None] = mapped_column(
        _enum(NotificationState, "payment_notification_state")
    )
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import MoneyType, UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class AccountType(enum.StrEnum):
    SAVINGS = "SAVINGS"
    CURRENT = "CURRENT"


class AccountStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class OperationKind(enum.StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    REVERSAL = "REVERSAL"
    DEV_SEED = "DEV_SEED"


class OperationStatus(enum.StrEnum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, validate_strings=True, length=20)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_accounts_balance_non_negative"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_number: Mapped[str] = mapped_column(String(16), unique=True)
    customer_id: Mapped[str] = mapped_column(String(36), index=True)
    account_type: Mapped[AccountType] = mapped_column(_enum(AccountType, "account_type"))
    currency: Mapped[str] = mapped_column(String(3))
    balance: Mapped[Decimal] = mapped_column(MoneyType, default=Decimal("0.00"))
    status: Mapped[AccountStatus] = mapped_column(
        _enum(AccountStatus, "account_status"), default=AccountStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AccountOperation(Base):
    """An idempotent balance change, keyed by a caller-supplied operation ID."""

    __tablename__ = "account_operations"

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[OperationKind] = mapped_column(_enum(OperationKind, "operation_kind"))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    currency: Mapped[str] = mapped_column(String(3))
    reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[OperationStatus] = mapped_column(_enum(OperationStatus, "operation_status"))
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    # A debit can be reversed at most once (unique constraint).
    reverses_operation_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

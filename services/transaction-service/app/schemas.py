from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import NotificationState, TransactionKind, TransactionStatus, WorkflowStep
from common.api import IDEMPOTENCY_KEY_PATTERN
from common.money import CurrencyCode, MoneyOut, PositiveAmount

ACCOUNT_ID = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_account_id: str = ACCOUNT_ID
    destination_account_id: str = ACCOUNT_ID
    amount: PositiveAmount
    currency: CurrencyCode
    description: str | None = Field(default=None, max_length=140)
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_KEY_PATTERN)


class PaymentDebitRequest(BaseModel):
    """Internal request from payment-service to debit a customer's account."""

    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    customer_id: str = Field(min_length=1, max_length=64)
    account_id: str = ACCOUNT_ID
    amount: PositiveAmount
    currency: CurrencyCode
    payee: str = Field(min_length=1, max_length=128)


class ReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: TransactionKind
    customer_id: str
    source_account_id: str
    destination_account_id: str | None
    payee: str | None
    amount: MoneyOut
    currency: str
    description: str | None
    status: TransactionStatus
    step: WorkflowStep
    failure_code: str | None
    failure_reason: str | None
    fraud_decision: str | None
    fraud_reason_codes: list[str] = []
    idempotency_key: str | None
    notification_status: NotificationState
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @field_validator("fraud_reason_codes", mode="before")
    @classmethod
    def _split_codes(cls, value):
        if value is None:
            return []
        return value.split(",") if isinstance(value, str) else value


class RecoveryReport(BaseModel):
    examined: int
    advanced: int
    notifications_sent: int

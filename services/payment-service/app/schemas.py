from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import NotificationState, PaymentStatus, PaymentStep, PaymentType
from common.api import IDEMPOTENCY_KEY_PATTERN
from common.money import CurrencyCode, MoneyOut, PositiveAmount


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    payment_type: PaymentType
    payee_name: str = Field(min_length=1, max_length=128)
    # Biller account / merchant ID (simulated).
    payee_reference: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    amount: PositiveAmount
    currency: CurrencyCode
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_KEY_PATTERN)

    @field_validator("payee_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("payee_name must not be blank")
        return value


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    account_id: str
    payment_type: PaymentType
    payee_name: str
    payee_reference: str
    amount: MoneyOut
    currency: str
    status: PaymentStatus
    step: PaymentStep
    failure_code: str | None
    failure_reason: str | None
    fraud_decision: str | None
    fraud_reason_codes: list[str] = []
    transaction_id: str | None
    provider_status: str | None
    provider_reference: str | None
    idempotency_key: str | None
    notification_status: NotificationState | None
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

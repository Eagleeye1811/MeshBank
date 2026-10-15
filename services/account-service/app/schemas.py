from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AccountStatus, AccountType, OperationKind, OperationStatus
from common.money import DEFAULT_CURRENCY, CurrencyCode, MoneyOut, PositiveAmount

OPERATION_ID = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_type: AccountType
    currency: CurrencyCode = DEFAULT_CURRENCY


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_number: str
    customer_id: str
    account_type: AccountType
    currency: str
    status: AccountStatus
    balance: MoneyOut
    created_at: datetime


class BalanceOut(BaseModel):
    account_id: str
    balance: MoneyOut
    currency: str
    as_of: datetime


class InternalAccountOut(BaseModel):
    """Account facts needed by other services to validate a workflow."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    currency: str
    status: AccountStatus


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = OPERATION_ID
    amount: PositiveAmount
    currency: CurrencyCode
    reference: str | None = Field(default=None, max_length=128)


class ReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = OPERATION_ID
    original_operation_id: str = OPERATION_ID
    reference: str | None = Field(default=None, max_length=128)


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: PositiveAmount
    operation_id: str | None = Field(
        default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$"
    )


class StatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AccountStatus


class OperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    operation_id: str
    account_id: str
    kind: OperationKind
    amount: MoneyOut
    currency: str
    status: OperationStatus
    rejection_code: str | None
    reverses_operation_id: str | None
    created_at: datetime
    replayed: bool = False

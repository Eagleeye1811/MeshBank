from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.rules import Decision
from common.money import DecimalAmount


class OperationType(StrEnum):
    TRANSFER = "TRANSFER"
    PAYMENT = "PAYMENT"


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")
    operation_type: OperationType
    customer_id: str = Field(min_length=1, max_length=64)
    source_account_id: str = Field(min_length=1, max_length=64)
    destination_account_id: str | None = Field(default=None, max_length=64)
    payee: str | None = Field(default=None, max_length=128)
    # Any finite decimal: non-positive amounts are a rule outcome, not a 422.
    amount: DecimalAmount
    currency: str = Field(min_length=1, max_length=8)


class EvaluationResponse(BaseModel):
    operation_id: str
    decision: Decision
    reason_codes: list[str]
    explanation: str
    rules_version: str
    evaluated_at: datetime
    replayed: bool = False

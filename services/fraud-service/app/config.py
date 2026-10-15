from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from common.configuration import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "fraud-service"
    database_url: str = "sqlite:///./data/fraud.db"
    # Amounts strictly above the review threshold are held for REVIEW.
    fraud_review_threshold: Decimal = Field(default=Decimal("50000.00"), gt=0)
    # Amounts strictly above the hard limit get FRAUD_HARD_LIMIT_ACTION.
    fraud_hard_limit: Decimal = Field(default=Decimal("200000.00"), gt=0)
    fraud_hard_limit_action: Literal["REJECT", "REVIEW"] = "REJECT"
    # Comma-separated payee identifiers that are always rejected (simulation).
    fraud_blocked_payees: str = ""

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> Self:
        if self.fraud_review_threshold > self.fraud_hard_limit:
            raise ValueError("FRAUD_REVIEW_THRESHOLD must not exceed FRAUD_HARD_LIMIT")
        return self

    def blocked_payees(self) -> frozenset[str]:
        return frozenset(
            p.strip().lower() for p in self.fraud_blocked_payees.split(",") if p.strip()
        )

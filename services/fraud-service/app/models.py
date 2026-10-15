from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class FraudEvaluation(Base):
    """A persisted decision, so re-evaluating an operation is stable even if
    thresholds change later."""

    __tablename__ = "fraud_evaluations"

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    operation_type: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    # Stored as text because rejected inputs may be negative or over-precise.
    amount: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(10))
    reason_codes: Mapped[str] = mapped_column(Text)  # comma-separated
    explanation: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

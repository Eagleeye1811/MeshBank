import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class NotificationType(enum.StrEnum):
    TRANSFER_SUCCEEDED = "TRANSFER_SUCCEEDED"
    TRANSFER_FAILED = "TRANSFER_FAILED"
    PAYMENT_SUCCEEDED = "PAYMENT_SUCCEEDED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    FRAUD_REVIEW = "FRAUD_REVIEW"


class DeliveryStatus(enum.StrEnum):
    PENDING = "PENDING"
    DELIVERED_SIMULATED = "DELIVERED_SIMULATED"
    FAILED_SIMULATED = "FAILED_SIMULATED"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Stable event key supplied by the producer; guarantees deduplication.
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", native_enum=False, length=32)
    )
    message: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(32), default="SIMULATED_LOG")
    # Persisted (created_at) and simulated delivery status are tracked separately.
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status", native_enum=False, length=32),
        default=DeliveryStatus.PENDING,
    )
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_delivery_error: Mapped[str | None] = mapped_column(String(255))

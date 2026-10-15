from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import DeliveryStatus, NotificationType


class CreateNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:\-]+$")
    customer_id: str = Field(min_length=1, max_length=64)
    type: NotificationType
    message: str = Field(min_length=1, max_length=500)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_key: str
    customer_id: str
    type: NotificationType
    message: str
    channel: str
    created_at: datetime
    delivery_status: DeliveryStatus
    delivery_attempts: int
    delivered_at: datetime | None
    replayed: bool = False

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.delivery import LogDeliveryAdapter, SimulatedDeliveryError
from app.models import DeliveryStatus, Notification
from app.schemas import CreateNotificationRequest
from common.api import PageParams, fingerprint
from common.database import utcnow
from common.errors import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger("notification.service")


class NotificationService:
    def __init__(self, session: Session, adapter: LogDeliveryAdapter) -> None:
        self.session = session
        self.adapter = adapter

    def create(self, request: CreateNotificationRequest) -> tuple[Notification, bool]:
        """Persist then attempt simulated delivery. Returns (notification, created)."""
        request_hash = fingerprint(request.model_dump(mode="json"))
        existing = self._by_event_key(request.event_key)
        if existing is not None:
            return self._dedupe(existing, request_hash), False
        notification = Notification(
            event_key=request.event_key,
            request_hash=request_hash,
            customer_id=request.customer_id,
            type=request.type,
            message=request.message,
            channel=self.adapter.channel,
        )
        self.session.add(notification)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self._by_event_key(request.event_key)
            if existing is None:
                raise
            return self._dedupe(existing, request_hash), False
        self.attempt_delivery(notification)
        return notification, True

    def attempt_delivery(self, notification: Notification) -> Notification:
        notification.delivery_attempts += 1
        try:
            self.adapter.deliver(notification)
        except (SimulatedDeliveryError, OSError) as exc:
            notification.delivery_status = DeliveryStatus.FAILED_SIMULATED
            notification.last_delivery_error = str(exc)[:255]
        else:
            notification.delivery_status = DeliveryStatus.DELIVERED_SIMULATED
            notification.delivered_at = utcnow()
            notification.last_delivery_error = None
        self.session.commit()
        logger.info(
            "notification delivery attempted",
            extra={
                "notification_id": notification.id,
                "type": notification.type.value,
                "delivery_status": notification.delivery_status.value,
                "attempt": notification.delivery_attempts,
            },
        )
        return notification

    def redeliver_pending(self, limit: int = 100) -> int:
        pending = self.session.scalars(
            select(Notification)
            .where(Notification.delivery_status != DeliveryStatus.DELIVERED_SIMULATED)
            .order_by(Notification.created_at)
            .limit(limit)
        ).all()
        for notification in pending:
            self.attempt_delivery(notification)
        return sum(n.delivery_status == DeliveryStatus.DELIVERED_SIMULATED for n in pending)

    def get_owned(self, notification_id: str, customer_id: str) -> Notification:
        notification = self.session.get(Notification, notification_id)
        if notification is None:
            raise NotFoundError("Notification not found", code="NOTIFICATION_NOT_FOUND")
        if notification.customer_id != customer_id:
            raise ForbiddenError("You do not have access to this notification")
        return notification

    def list_for_customer(
        self, customer_id: str, page: PageParams
    ) -> tuple[list[Notification], int]:
        query = select(Notification).where(Notification.customer_id == customer_id)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = self.session.scalars(
            query.order_by(Notification.created_at.desc(), Notification.id)
            .limit(page.limit)
            .offset(page.offset)
        ).all()
        return list(items), total

    def _by_event_key(self, event_key: str) -> Notification | None:
        return self.session.scalar(select(Notification).where(Notification.event_key == event_key))

    @staticmethod
    def _dedupe(existing: Notification, request_hash: str) -> Notification:
        if existing.request_hash != request_hash:
            raise ConflictError(
                "Event key was already used with a different notification",
                code="EVENT_KEY_CONFLICT",
            )
        return existing

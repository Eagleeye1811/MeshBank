"""Simulated delivery adapter. Writes to a local log file; never contacts a
real email, SMS or push provider."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.models import Notification
from common.database import utcnow


class SimulatedDeliveryError(Exception):
    pass


class LogDeliveryAdapter:
    channel = "SIMULATED_LOG"

    def __init__(self, path: str, failing_types: frozenset[str] = frozenset()) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.failing_types = failing_types
        self._lock = threading.Lock()

    def deliver(self, notification: Notification) -> None:
        if "*" in self.failing_types or notification.type.value in self.failing_types:
            raise SimulatedDeliveryError("Simulated delivery failure")
        line = json.dumps(
            {
                "delivered_at": utcnow().isoformat(),
                "notification_id": notification.id,
                "customer_id": notification.customer_id,
                "type": notification.type.value,
                "message": notification.message,
            }
        )
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

"""Alembic environment for the notification-service (its own database and history)."""

from app.models import Base
from common.migrations import run_alembic_env

run_alembic_env(
    Base.metadata, service="notification-service", default_url="sqlite:///./data/notifications.db"
)

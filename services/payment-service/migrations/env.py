"""Alembic environment for the payment-service (its own database and history)."""

from app.models import Base
from common.migrations import run_alembic_env

run_alembic_env(
    Base.metadata, service="payment-service", default_url="sqlite:///./data/payments.db"
)

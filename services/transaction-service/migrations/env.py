"""Alembic environment for the transaction-service (its own database and history)."""

from app.models import Base
from common.migrations import run_alembic_env

run_alembic_env(
    Base.metadata, service="transaction-service", default_url="sqlite:///./data/transactions.db"
)

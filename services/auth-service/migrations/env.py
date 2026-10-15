"""Alembic environment for the auth-service (its own database and history)."""

from app.models import Base
from common.migrations import run_alembic_env

run_alembic_env(Base.metadata, service="auth-service", default_url="sqlite:///./data/auth.db")

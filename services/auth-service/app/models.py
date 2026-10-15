import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.database import UTCDateTime, utcnow


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class LoginThrottle(Base):
    """Failed-login counter for one throttle key in a fixed time window.

    Keys are SHA-256 hashes (raw IPs and emails are not stored here). Rows
    simply expire with their window: there is no permanent lockout.
    """

    __tablename__ = "login_throttle"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime)

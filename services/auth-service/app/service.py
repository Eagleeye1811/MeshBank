"""Authentication business logic, independent of HTTP routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.passwords import PasswordService
from common.errors import ConflictError, UnauthorizedError
from common.security import TokenClaims, create_access_token, decode_access_token

logger = logging.getLogger("auth.service")

INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: datetime
    expires_in: int


class AuthService:
    def __init__(self, session: Session, settings: Settings, passwords: PasswordService) -> None:
        self.session = session
        self.settings = settings
        self.passwords = passwords

    def register(self, *, email: str, password: str, full_name: str) -> User:
        if self.session.scalar(select(User.id).where(User.email == email)) is not None:
            raise ConflictError("An account with this email already exists", code="USER_EXISTS")
        user = User(email=email, full_name=full_name, password_hash=self.passwords.hash(password))
        self.session.add(user)
        try:
            self.session.commit()
        except IntegrityError as exc:  # concurrent registration with the same email
            self.session.rollback()
            raise ConflictError(
                "An account with this email already exists", code="USER_EXISTS"
            ) from exc
        logger.info("user registered", extra={"customer_id": user.id})
        return user

    def authenticate(self, *, email: str, password: str) -> User:
        user = self.session.scalar(select(User).where(User.email == email.strip().lower()))
        valid = self.passwords.verify(user.password_hash if user else None, password)
        if user is None or not valid or not user.is_active:
            logger.info("login failed")
            raise UnauthorizedError(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")
        if self.passwords.needs_rehash(user.password_hash):
            user.password_hash = self.passwords.hash(password)
            self.session.commit()
        logger.info("login succeeded", extra={"customer_id": user.id})
        return user

    def issue_token(self, user: User) -> IssuedToken:
        token, expires_at = create_access_token(
            self.settings,
            signing_key=self.settings.signing_key(),
            subject=user.id,
            email=user.email,
            ttl_seconds=self.settings.access_token_ttl_seconds,
        )
        return IssuedToken(token, expires_at, self.settings.access_token_ttl_seconds)

    def active_user_for_claims(self, claims: TokenClaims) -> User:
        user = self.session.get(User, claims.customer_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("The access token is invalid", code="INVALID_TOKEN")
        return user

    def verify_token(self, token: str) -> tuple[TokenClaims, User]:
        claims = decode_access_token(self.settings, token)
        return claims, self.active_user_for_claims(claims)

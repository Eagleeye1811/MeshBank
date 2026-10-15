"""Typed, validated configuration shared by every service.

Settings are read from environment variables (and an optional ``.env`` file in
the working directory). Production configuration fails fast when secrets or
keys are missing or obviously unsafe. Development may use the clearly-labelled
internal-token fallback and locally generated ``dev-*`` JWT keys.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.keys import DEV_KID_PREFIX, KeyConfigError, KeyRing, load_keyring

logger = logging.getLogger(__name__)

# Development-only fallback for the internal service token; rejected in production.
DEV_INTERNAL_API_TOKEN = "dev-only-insecure-internal-token-do-not-use-in-production"  # noqa: S105
_UNSAFE_MARKERS = ("dev-only", "change-me", "changeme", "placeholder", "example", "secret123")
MIN_SECRET_LENGTH = 32


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _is_unsafe_secret(value: str | None) -> bool:
    if not value or len(value) < MIN_SECRET_LENGTH:
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in _UNSAFE_MARKERS)


def normalize_database_url(url: str) -> str:
    """Use the psycopg 3 driver for PostgreSQL URLs given without a driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


MIN_DB_PASSWORD_LENGTH = 16
_WEAK_DB_PASSWORDS = {"", "postgres", "password", "admin", "root", "meshbank"}


def database_problems(url: str, migration_mode: str) -> list[str]:
    """Production database checks (never echo the URL: it may hold a password)."""
    from sqlalchemy.engine import make_url

    problems = []
    if migration_mode == "create_all":
        problems.append("DB_MIGRATION_MODE=create_all is not allowed in production")
    try:
        parsed = make_url(url)
    except Exception:
        return [*problems, "DATABASE_URL is not a valid database URL"]
    if parsed.get_backend_name() == "sqlite":
        problems.append("DATABASE_URL must point to PostgreSQL in production, not SQLite")
    else:
        password = parsed.password or ""
        weak = (
            password.lower() in _WEAK_DB_PASSWORDS
            or len(password) < MIN_DB_PASSWORD_LENGTH
            or any(marker in password.lower() for marker in _UNSAFE_MARKERS)
        )
        if weak:
            problems.append("DATABASE_URL must use a strong, non-placeholder password")
    return problems


class BaseServiceSettings(BaseSettings):
    """Settings every service needs."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "meshbank-service"
    # Release identity, baked into the image at build time (SERVICE_VERSION build
    # arg) and reported in /health and the X-Service-Version response header.
    service_version: str = Field(default="v1", pattern=r"^v[0-9]+$")
    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    database_url: str = "sqlite:///./data/service.db"
    # upgrade: run Alembic migrations to head at startup (development default)
    # check:   refuse to start unless the schema is already at head (production;
    #          migrations run as an explicit step, e.g. a Kubernetes init container)
    # create_all: legacy SQLite shortcut without migration history (not in production)
    db_migration_mode: Literal["upgrade", "check", "create_all"] = "upgrade"

    # RS256 token verification. Directory of public keys named <kid>.pem
    # (generate local ones with `make dev-keys`). The algorithm is fixed to
    # RS256 and never taken from the token.
    jwt_public_keys_dir: str | None = None
    jwt_issuer: str = "meshbank-auth"
    jwt_audience: str = "meshbank-api"
    jwt_leeway_seconds: int = Field(default=0, ge=0, le=60)

    # Shared secret identifying internal callers. This is a Phase 1 stand-in for
    # the workload identity that mTLS will provide in a later phase.
    internal_api_token: SecretStr | None = None

    http_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    http_max_attempts: int = Field(default=2, ge=1, le=5)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    _keyring: KeyRing | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _apply_secret_policy(self) -> Self:
        # An empty variable (e.g. ``INTERNAL_API_TOKEN=`` in Compose) counts as unset.
        if self.internal_api_token is not None and not self.internal_api_token.get_secret_value():
            self.internal_api_token = None
        if not self.jwt_public_keys_dir:
            raise ValueError(
                "JWT_PUBLIC_KEYS_DIR is required (run 'make dev-keys' for local development)"
            )
        try:
            self._keyring = load_keyring(self.jwt_public_keys_dir)
        except KeyConfigError as exc:
            raise ValueError(str(exc)) from exc
        if self.app_env == AppEnv.PRODUCTION:
            problems = []
            if _is_unsafe_secret(
                self.internal_api_token.get_secret_value() if self.internal_api_token else None
            ):
                problems.append(
                    "INTERNAL_API_TOKEN must be set to a random value of at least 32 characters"
                )
            if any(kid.startswith(DEV_KID_PREFIX) for kid in self._keyring.kids):
                problems.append("development JWT keys (kid 'dev-*') are not allowed in production")
            problems.extend(database_problems(self.database_url, self.db_migration_mode))
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        elif self.internal_api_token is None:
            self.internal_api_token = SecretStr(DEV_INTERNAL_API_TOKEN)
        return self

    def jwt_keyring(self) -> KeyRing:
        if self._keyring is None:  # unreachable: loaded by the validator
            raise RuntimeError("JWT keyring is not loaded")
        return self._keyring

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    def internal_token_value(self) -> str:
        if self.internal_api_token is None:  # unreachable: the validator always sets it
            raise RuntimeError("Internal API token is not configured")
        return self.internal_api_token.get_secret_value()

    def uses_development_secrets(self) -> bool:
        return self.internal_token_value() == DEV_INTERNAL_API_TOKEN or any(
            kid.startswith(DEV_KID_PREFIX) for kid in self.jwt_keyring().kids
        )

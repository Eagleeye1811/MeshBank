"""JWT handling and request authentication dependencies.

Customer identity always comes from a verified token, never from a customer ID
supplied in a URL or body. Internal routes are protected by a separate shared
internal token: customer authentication and service identity are distinct
concerns (mTLS-based service identity replaces the shared token later).
"""

from __future__ import annotations

import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.configuration import BaseServiceSettings
from common.errors import ForbiddenError, UnauthorizedError
from common.keys import SigningKey

INTERNAL_TOKEN_HEADER = "X-Internal-Token"  # noqa: S105 (header name)
CALLER_SERVICE_HEADER = "X-Caller-Service"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105 (claim value)
JWT_ALGORITHM = "RS256"
logger = logging.getLogger("meshbank.security")


@dataclass(frozen=True)
class TokenClaims:
    customer_id: str
    email: str | None
    issued_at: datetime
    expires_at: datetime
    token_id: str


def create_access_token(
    settings: BaseServiceSettings,
    *,
    signing_key: SigningKey,
    subject: str,
    email: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=ttl_seconds)
    claims = {
        "sub": subject,
        "email": email,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
        "typ": ACCESS_TOKEN_TYPE,
    }
    token = jwt.encode(
        claims, signing_key.private_key, algorithm=JWT_ALGORITHM, headers={"kid": signing_key.kid}
    )
    return token, expires


def _invalid() -> UnauthorizedError:
    return UnauthorizedError("The access token is invalid", code="INVALID_TOKEN")


def decode_access_token(settings: BaseServiceSettings, token: str) -> TokenClaims:
    """Verify a customer token.

    The key is selected by the ``kid`` header from the configured keyring and
    the algorithm is fixed server-side to RS256: a token can never choose its
    own algorithm (no ``none``, no HS256 with a public key as the secret).
    Issuer, audience, expiry, not-before and token type are all enforced.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise _invalid() from exc
    kid = header.get("kid")
    if header.get("alg") != JWT_ALGORITHM or not isinstance(kid, str):
        raise _invalid()
    key = settings.jwt_keyring().get(kid)
    if key is None:
        logger.info("token signed with unknown key id")
        raise _invalid()
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=settings.jwt_leeway_seconds,
            options={"require": ["sub", "iss", "aud", "exp", "iat", "nbf", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("The access token has expired", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise _invalid() from exc
    if claims.get("typ") != ACCESS_TOKEN_TYPE or not isinstance(claims.get("sub"), str):
        raise _invalid()
    return TokenClaims(
        customer_id=claims["sub"],
        email=claims.get("email"),
        issued_at=datetime.fromtimestamp(claims["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        token_id=claims["jti"],
    )


_bearer = HTTPBearer(auto_error=False)


def get_settings_from_request(request: Request) -> BaseServiceSettings:
    return request.app.state.settings


def get_current_customer(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenClaims:
    """FastAPI dependency returning verified claims for the calling customer."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Authentication is required", code="AUTHENTICATION_REQUIRED")
    return decode_access_token(get_settings_from_request(request), credentials.credentials)


def require_internal_caller(request: Request) -> str:
    """FastAPI dependency for internal routes. Returns the caller service name."""
    supplied = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    expected = get_settings_from_request(request).internal_token_value()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ForbiddenError(
            "This endpoint is restricted to internal services", code="INTERNAL_ONLY"
        )
    return request.headers.get(CALLER_SERVICE_HEADER, "unknown")


def ensure_same_customer(claims: TokenClaims, customer_id: str) -> None:
    if claims.customer_id != customer_id:
        raise ForbiddenError("You may only access your own resources", code="FORBIDDEN")

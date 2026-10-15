import json

from fastapi import APIRouter, Depends, Request
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.orm import Session

from app.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
    VerifyRequest,
    VerifyResponse,
)
from app.service import AuthService
from app.throttle import LoginThrottler
from common.client_ip import resolve_client_ip
from common.database import get_session
from common.errors import UnauthorizedError
from common.security import TokenClaims, get_current_customer

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(request: Request, session: Session = Depends(get_session)) -> AuthService:
    return AuthService(session, request.app.state.settings, request.app.state.passwords)


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    return service.register(email=body.email, password=body.password, full_name=body.full_name)


def get_throttler(request: Request, session: Session = Depends(get_session)) -> LoginThrottler:
    return LoginThrottler(session, request.app.state.settings, request.app.state.clock)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={429: {"description": "Too many failed attempts; see Retry-After"}},
)
def login(
    body: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    throttler: LoginThrottler = Depends(get_throttler),
):
    settings = request.app.state.settings
    ip = resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
        settings.trusted_proxies(),
    )
    throttler.check(ip, body.email)  # before any password hashing work
    try:
        user = service.authenticate(email=body.email, password=body.password)
    except UnauthorizedError:
        throttler.record_failure(ip, body.email)
        raise
    throttler.record_success(ip, body.email)
    issued = service.issue_token(user)
    return TokenResponse(
        access_token=issued.token, expires_in=issued.expires_in, expires_at=issued.expires_at
    )


@router.get("/me", response_model=UserOut)
def me(
    claims: TokenClaims = Depends(get_current_customer),
    service: AuthService = Depends(get_auth_service),
):
    return service.active_user_for_claims(claims)


@router.post("/verify", response_model=VerifyResponse)
def verify(body: VerifyRequest, service: AuthService = Depends(get_auth_service)):
    """Verify a token against this service, including that the user is still active."""
    claims, user = service.verify_token(body.token)
    return VerifyResponse(
        valid=True, customer_id=user.id, email=user.email, expires_at=claims.expires_at
    )


jwks_router = APIRouter(tags=["auth"])


@jwks_router.get("/.well-known/jwks.json")
def jwks(request: Request) -> dict:
    """Public verification keys (JWK set). Other services are configured with
    the same public keys as files; this endpoint exists for tooling/inspection."""
    keyring = request.app.state.settings.jwt_keyring()
    keys = []
    for kid in keyring.kids:
        jwk = json.loads(RSAAlgorithm.to_jwk(keyring.get(kid)))
        keys.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": keys}

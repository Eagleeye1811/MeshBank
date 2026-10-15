"""HTTP routes. Public routes require a customer JWT; ``/internal`` routes
require the internal service token and are never meant to be exposed through a
public gateway."""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.schemas import (
    AccountOut,
    BalanceOut,
    CreateAccountRequest,
    InternalAccountOut,
    OperationOut,
    OperationRequest,
    ReversalRequest,
    SeedRequest,
    StatusChangeRequest,
)
from app.service import AccountService, OperationResult
from common.api import Page, PageParams, page_params
from common.database import get_session, utcnow
from common.security import (
    TokenClaims,
    ensure_same_customer,
    get_current_customer,
    require_internal_caller,
)


def get_service(session: Session = Depends(get_session)) -> AccountService:
    return AccountService(session)


def _operation_out(result: OperationResult) -> OperationOut:
    out = OperationOut.model_validate(result.operation)
    out.replayed = result.replayed
    return out


public = APIRouter(tags=["accounts"])


@public.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    body: CreateAccountRequest,
    claims: TokenClaims = Depends(get_current_customer),
    service: AccountService = Depends(get_service),
):
    return service.create_account(
        customer_id=claims.customer_id, account_type=body.account_type, currency=body.currency
    )


@public.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(
    account_id: UUID,
    claims: TokenClaims = Depends(get_current_customer),
    service: AccountService = Depends(get_service),
):
    return service.get_owned_account(str(account_id), claims.customer_id)


@public.get("/accounts/{account_id}/balance", response_model=BalanceOut)
def get_balance(
    account_id: UUID,
    claims: TokenClaims = Depends(get_current_customer),
    service: AccountService = Depends(get_service),
):
    account = service.get_owned_account(str(account_id), claims.customer_id)
    return BalanceOut(
        account_id=account.id, balance=account.balance, currency=account.currency, as_of=utcnow()
    )


@public.get("/customers/{customer_id}/accounts", response_model=Page[AccountOut])
def list_accounts(
    customer_id: str,
    page: PageParams = Depends(page_params),
    claims: TokenClaims = Depends(get_current_customer),
    service: AccountService = Depends(get_service),
):
    ensure_same_customer(claims, customer_id)
    items, total = service.list_accounts(customer_id, page)
    return Page[AccountOut](
        items=[AccountOut.model_validate(a) for a in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


internal = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_caller)]
)


@internal.get("/accounts/{account_id}", response_model=InternalAccountOut)
def internal_get_account(account_id: str, service: AccountService = Depends(get_service)):
    return service.get_account(account_id)


@internal.post("/accounts/{account_id}/debit", response_model=OperationOut)
def internal_debit(
    account_id: str, body: OperationRequest, service: AccountService = Depends(get_service)
):
    """Idempotent debit. Business rejections (e.g. insufficient funds) are
    recorded and returned with ``status=REJECTED`` and a ``rejection_code``."""
    return _operation_out(
        service.debit(account_id, body.operation_id, body.amount, body.currency, body.reference)
    )


@internal.post("/accounts/{account_id}/credit", response_model=OperationOut)
def internal_credit(
    account_id: str, body: OperationRequest, service: AccountService = Depends(get_service)
):
    return _operation_out(
        service.credit(account_id, body.operation_id, body.amount, body.currency, body.reference)
    )


@internal.post("/accounts/{account_id}/reversals", response_model=OperationOut)
def internal_reverse(
    account_id: str, body: ReversalRequest, service: AccountService = Depends(get_service)
):
    return _operation_out(
        service.reverse(account_id, body.operation_id, body.original_operation_id, body.reference)
    )


@internal.get("/operations/{operation_id}", response_model=OperationOut)
def internal_get_operation(operation_id: str, service: AccountService = Depends(get_service)):
    return OperationOut.model_validate(service.get_operation(operation_id))


@internal.put("/accounts/{account_id}/status", response_model=InternalAccountOut)
def internal_set_status(
    account_id: str, body: StatusChangeRequest, service: AccountService = Depends(get_service)
):
    """Operator action (e.g. freezing an account)."""
    return service.set_status(account_id, body.status)


dev = APIRouter(
    prefix="/internal/dev",
    tags=["development only"],
    dependencies=[Depends(require_internal_caller)],
)


@dev.post("/accounts/{account_id}/seed", response_model=OperationOut)
def dev_seed_balance(
    account_id: str,
    body: SeedRequest,
    service: AccountService = Depends(get_service),
):
    """DEVELOPMENT ONLY. Mounted only when APP_ENV != production and
    DEV_SEED_ENABLED=true. Adds simulated funds for local testing."""
    operation_id = body.operation_id or f"dev-seed-{uuid.uuid4()}"
    return _operation_out(service.seed(account_id, operation_id, body.amount))

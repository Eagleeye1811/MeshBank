from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionStatus
from app.schemas import (
    PaymentDebitRequest,
    RecoveryReport,
    ReversalRequest,
    TransactionOut,
    TransferRequest,
)
from app.workflow import UNAVAILABLE_FAILURES, TransferWorkflow
from common.api import Page, PageParams, page_params
from common.database import get_session
from common.errors import ForbiddenError, NotFoundError, error_body
from common.security import (
    TokenClaims,
    ensure_same_customer,
    get_current_customer,
    require_internal_caller,
)

REPLAY_HEADER = "Idempotent-Replayed"


def get_workflow(request: Request, session: Session = Depends(get_session)) -> TransferWorkflow:
    state = request.app.state
    return TransferWorkflow(
        session,
        accounts=state.account_client,
        fraud=state.fraud_client,
        notifications=state.notification_client,
        lease_seconds=state.settings.workflow_lease_seconds,
    )


def transfer_response(tx: Transaction, created: bool) -> JSONResponse:
    """201: finished (COMPLETED/REJECTED/FAILED-compensated); 202: accepted but not
    final (PROCESSING, or UNDER_REVIEW); 503: failed because a dependency was
    down and no funds moved; 200: idempotent replay of an earlier request."""
    body = TransactionOut.model_validate(tx).model_dump(mode="json")
    if not created:
        return JSONResponse(status_code=200, content=body, headers={REPLAY_HEADER: "true"})
    if tx.status == TransactionStatus.FAILED and tx.failure_code in UNAVAILABLE_FAILURES:
        return JSONResponse(
            status_code=503,
            content=error_body(
                tx.failure_code,
                tx.failure_reason or "A dependency is unavailable",
                {"transaction_id": tx.id, "status": tx.status.value},
            ),
        )
    if tx.status in (TransactionStatus.PROCESSING, TransactionStatus.UNDER_REVIEW):
        return JSONResponse(status_code=202, content=body)
    return JSONResponse(status_code=201, content=body)


public = APIRouter(tags=["transactions"])


@public.post(
    "/transfers",
    response_model=TransactionOut,
    status_code=201,
    responses={
        200: {"description": "Idempotent replay"},
        202: {"description": "Accepted; PROCESSING or UNDER_REVIEW"},
        503: {"description": "Dependency unavailable; no funds moved"},
    },
)
def create_transfer(
    body: TransferRequest,
    claims: TokenClaims = Depends(get_current_customer),
    workflow: TransferWorkflow = Depends(get_workflow),
):
    tx, created = workflow.submit_transfer(claims.customer_id, body)
    return transfer_response(tx, created)


@public.get("/transactions/{transaction_id}", response_model=TransactionOut)
def get_transaction(
    transaction_id: UUID,
    claims: TokenClaims = Depends(get_current_customer),
    session: Session = Depends(get_session),
):
    tx = session.get(Transaction, str(transaction_id))
    if tx is None:
        raise NotFoundError("Transaction not found", code="TRANSACTION_NOT_FOUND")
    if tx.customer_id != claims.customer_id:
        raise ForbiddenError("You do not have access to this transaction")
    return TransactionOut.model_validate(tx)


@public.get("/customers/{customer_id}/transactions", response_model=Page[TransactionOut])
def list_transactions(
    customer_id: str,
    page: PageParams = Depends(page_params),
    claims: TokenClaims = Depends(get_current_customer),
    session: Session = Depends(get_session),
):
    ensure_same_customer(claims, customer_id)
    query = select(Transaction).where(Transaction.customer_id == customer_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(Transaction.created_at.desc(), Transaction.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[TransactionOut](
        items=[TransactionOut.model_validate(t) for t in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


internal = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_caller)]
)


@internal.post("/payment-debits", response_model=TransactionOut)
def create_payment_debit(
    body: PaymentDebitRequest, workflow: TransferWorkflow = Depends(get_workflow)
):
    """Idempotent per payment_id. Returns the transaction in its current state
    (COMPLETED, REJECTED, FAILED or PROCESSING)."""
    tx, created = workflow.submit_payment_debit(body)
    return JSONResponse(
        status_code=201 if created else 200,
        content=TransactionOut.model_validate(tx).model_dump(mode="json"),
    )


@internal.get("/transactions/{transaction_id}", response_model=TransactionOut)
def internal_get_transaction(transaction_id: str, session: Session = Depends(get_session)):
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise NotFoundError("Transaction not found", code="TRANSACTION_NOT_FOUND")
    return TransactionOut.model_validate(tx)


@internal.post("/transactions/{transaction_id}/reversal", response_model=TransactionOut)
def reverse_payment_debit(
    transaction_id: str,
    body: ReversalRequest,
    session: Session = Depends(get_session),
    workflow: TransferWorkflow = Depends(get_workflow),
):
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise NotFoundError("Transaction not found", code="TRANSACTION_NOT_FOUND")
    return TransactionOut.model_validate(workflow.reverse_payment_debit(tx, body.reason))


@internal.post("/recovery/run", response_model=RecoveryReport)
def run_recovery(workflow: TransferWorkflow = Depends(get_workflow)):
    """Operator action: resume unfinished workflows and resend notifications."""
    return workflow.recover()

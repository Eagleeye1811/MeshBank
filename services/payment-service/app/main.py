"""Payment Service: simulated bill and merchant payments.

Run with ``uvicorn --factory app.main:create_app``.
"""

import logging
import threading
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients import (
    HttpAccountClient,
    HttpFraudClient,
    HttpNotificationClient,
    HttpTransactionClient,
)
from app.config import Settings
from app.models import Base, Payment, PaymentStatus
from app.provider import SimulatedProvider
from app.schemas import PaymentOut, PaymentRequest, RecoveryReport
from app.workflow import UNAVAILABLE_FAILURES, PaymentWorkflow
from common.api import Page, PageParams, page_params
from common.database import get_session
from common.errors import ForbiddenError, NotFoundError, error_body
from common.http_client import InternalClient
from common.security import (
    TokenClaims,
    ensure_same_customer,
    get_current_customer,
    require_internal_caller,
)
from common.service_app import create_service_app

logger = logging.getLogger("payment.app")

SERVICE_DIR = Path(__file__).resolve().parents[1]


def workflow_for(state, session: Session) -> PaymentWorkflow:
    return PaymentWorkflow(
        session,
        accounts=state.account_client,
        fraud=state.fraud_client,
        transactions=state.transaction_client,
        notifications=state.notification_client,
        provider=state.provider,
        lease_seconds=state.settings.workflow_lease_seconds,
    )


def get_workflow(request: Request, session: Session = Depends(get_session)) -> PaymentWorkflow:
    return workflow_for(request.app.state, session)


router = APIRouter(tags=["payments"])


@router.post(
    "/payments",
    response_model=PaymentOut,
    status_code=201,
    responses={
        200: {"description": "Idempotent replay"},
        202: {"description": "Accepted; PROCESSING or UNDER_REVIEW"},
        503: {"description": "Dependency unavailable; customer not charged"},
    },
)
def create_payment(
    body: PaymentRequest,
    claims: TokenClaims = Depends(get_current_customer),
    workflow: PaymentWorkflow = Depends(get_workflow),
):
    payment, created = workflow.submit(claims.customer_id, body)
    content = PaymentOut.model_validate(payment).model_dump(mode="json")
    if not created:
        return JSONResponse(
            status_code=200, content=content, headers={"Idempotent-Replayed": "true"}
        )
    if payment.status == PaymentStatus.FAILED and payment.failure_code in UNAVAILABLE_FAILURES:
        return JSONResponse(
            status_code=503,
            content=error_body(
                payment.failure_code,
                payment.failure_reason or "A dependency is unavailable",
                {"payment_id": payment.id, "status": payment.status.value},
            ),
        )
    if payment.status in (PaymentStatus.PROCESSING, PaymentStatus.UNDER_REVIEW):
        return JSONResponse(status_code=202, content=content)
    return JSONResponse(status_code=201, content=content)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: UUID,
    claims: TokenClaims = Depends(get_current_customer),
    session: Session = Depends(get_session),
):
    payment = session.get(Payment, str(payment_id))
    if payment is None:
        raise NotFoundError("Payment not found", code="PAYMENT_NOT_FOUND")
    if payment.customer_id != claims.customer_id:
        raise ForbiddenError("You do not have access to this payment")
    return PaymentOut.model_validate(payment)


@router.get("/customers/{customer_id}/payments", response_model=Page[PaymentOut])
def list_payments(
    customer_id: str,
    page: PageParams = Depends(page_params),
    claims: TokenClaims = Depends(get_current_customer),
    session: Session = Depends(get_session),
):
    ensure_same_customer(claims, customer_id)
    query = select(Payment).where(Payment.customer_id == customer_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(Payment.created_at.desc(), Payment.id).limit(page.limit).offset(page.offset)
    ).all()
    return Page[PaymentOut](
        items=[PaymentOut.model_validate(p) for p in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/internal/recovery/run",
    response_model=RecoveryReport,
    tags=["internal"],
    dependencies=[Depends(require_internal_caller)],
)
def run_recovery(workflow: PaymentWorkflow = Depends(get_workflow)):
    """Operator action: resume unfinished payments and resend notifications."""
    return workflow.recover()


class RecoveryLoop:
    def __init__(self, app: FastAPI, interval: float) -> None:
        self.app, self.interval = app, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="recovery-loop", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                with self.app.state.db.session() as session:
                    workflow_for(self.app.state, session).recover()
            except Exception:
                logger.exception("recovery pass failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    def client(target: str, url: str) -> InternalClient:
        return InternalClient(
            target=target,
            base_url=url,
            caller=settings.service_name,
            internal_token=settings.internal_token_value(),
            timeout_seconds=settings.http_timeout_seconds,
            max_attempts=settings.http_max_attempts,
        )

    http_clients = [
        client("account-service", settings.account_service_url),
        client("fraud-service", settings.fraud_service_url),
        # The transaction service may itself call the account service, so give
        # it a longer budget than a single hop.
        InternalClient(
            target="transaction-service",
            base_url=settings.transaction_service_url,
            caller=settings.service_name,
            internal_token=settings.internal_token_value(),
            timeout_seconds=settings.http_timeout_seconds * 4,
            max_attempts=settings.http_max_attempts,
        ),
        client("notification-service", settings.notification_service_url),
    ]

    def on_startup(app: FastAPI) -> None:
        if settings.recovery_interval_seconds > 0:
            app.state.recovery_loop = RecoveryLoop(app, settings.recovery_interval_seconds)
            app.state.recovery_loop.start()

    def on_shutdown(app: FastAPI) -> None:
        if loop := getattr(app.state, "recovery_loop", None):
            loop.stop()
        for c in http_clients:
            c.close()

    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Payment Service",
        description="Simulated bill and merchant payments. No real payment gateway is used.",
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    app.state.account_client = HttpAccountClient(http_clients[0])
    app.state.fraud_client = HttpFraudClient(http_clients[1])
    app.state.transaction_client = HttpTransactionClient(http_clients[2])
    app.state.notification_client = HttpNotificationClient(http_clients[3])
    app.state.provider = SimulatedProvider(
        decline_prefix=settings.payment_provider_decline_prefix,
        max_amount=settings.payment_provider_max_amount,
    )
    app.include_router(router)
    return app


def recover_cli() -> int:
    """``python -m app.main`` runs a single recovery pass."""
    app = create_app()
    with app.state.db.session() as session:
        report = workflow_for(app.state, session).recover()
    print(
        f"Recovery pass: examined={report.examined} advanced={report.advanced} "
        f"notifications_sent={report.notifications_sent}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(recover_cli())

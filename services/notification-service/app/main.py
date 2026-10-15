"""Notification Service: persisted notifications with simulated delivery.

Run with ``uvicorn --factory app.main:create_app``.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.delivery import LogDeliveryAdapter
from app.models import Base
from app.schemas import CreateNotificationRequest, NotificationOut
from app.service import NotificationService
from common.api import Page, PageParams, page_params
from common.database import get_session
from common.security import (
    TokenClaims,
    ensure_same_customer,
    get_current_customer,
    require_internal_caller,
)
from common.service_app import create_service_app

router = APIRouter(tags=["notifications"])

SERVICE_DIR = Path(__file__).resolve().parents[1]


def get_service(request: Request, session: Session = Depends(get_session)) -> NotificationService:
    return NotificationService(session, request.app.state.delivery_adapter)


@router.post(
    "/notifications",
    response_model=NotificationOut,
    status_code=201,
    dependencies=[Depends(require_internal_caller)],
    responses={200: {"description": "Duplicate event key; existing notification returned"}},
)
def create_notification(
    body: CreateNotificationRequest, service: NotificationService = Depends(get_service)
):
    """Internal-only. Deduplicated by ``event_key``. Returns 201 when created and
    200 when the event was already recorded."""
    notification, created = service.create(body)
    out = NotificationOut.model_validate(notification)
    out.replayed = not created
    return JSONResponse(status_code=201 if created else 200, content=out.model_dump(mode="json"))


@router.get("/notifications/{notification_id}", response_model=NotificationOut)
def get_notification(
    notification_id: UUID,
    claims: TokenClaims = Depends(get_current_customer),
    service: NotificationService = Depends(get_service),
):
    return service.get_owned(str(notification_id), claims.customer_id)


@router.get("/customers/{customer_id}/notifications", response_model=Page[NotificationOut])
def list_notifications(
    customer_id: str,
    page: PageParams = Depends(page_params),
    claims: TokenClaims = Depends(get_current_customer),
    service: NotificationService = Depends(get_service),
):
    ensure_same_customer(claims, customer_id)
    items, total = service.list_for_customer(customer_id, page)
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(n) for n in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Notification Service",
        description="Persisted banking notifications with simulated (log-file) delivery.",
    )
    app.state.delivery_adapter = LogDeliveryAdapter(
        settings.notification_delivery_log, settings.failing_types()
    )
    app.include_router(router)
    return app

"""Fraud Detection Service: deterministic, configurable rule evaluation.

Run with ``uvicorn --factory app.main:create_app``.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base
from app.schemas import EvaluationRequest, EvaluationResponse
from app.service import FraudService, policy_from_settings
from common.database import get_session
from common.security import require_internal_caller
from common.service_app import create_service_app

router = APIRouter(tags=["fraud"], dependencies=[Depends(require_internal_caller)])

SERVICE_DIR = Path(__file__).resolve().parents[1]


@router.post("/evaluate", response_model=EvaluationResponse)
def evaluate(body: EvaluationRequest, request: Request, session: Session = Depends(get_session)):
    """Internal-only. Re-evaluating the same operation ID with the same payload
    returns the stored decision; a different payload is a 409 conflict."""
    return FraudService(session, request.app.state.fraud_policy).evaluate(body)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = create_service_app(
        settings=settings,
        base=Base,
        service_dir=SERVICE_DIR,
        title="MeshBank Fraud Detection Service",
        description="Deterministic rule-based risk evaluation for simulated operations.",
    )
    app.state.fraud_policy = policy_from_settings(settings)
    app.include_router(router)
    return app

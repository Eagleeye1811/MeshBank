from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import FraudEvaluation
from app.rules import RULES_VERSION, Decision, FraudPolicy, Operation, evaluate
from app.schemas import EvaluationRequest, EvaluationResponse
from common.api import fingerprint
from common.errors import ConflictError

logger = logging.getLogger("fraud.service")


def policy_from_settings(settings: Settings) -> FraudPolicy:
    return FraudPolicy(
        review_threshold=settings.fraud_review_threshold,
        hard_limit=settings.fraud_hard_limit,
        hard_limit_action=Decision(settings.fraud_hard_limit_action),
        blocked_payees=settings.blocked_payees(),
    )


class FraudService:
    def __init__(self, session: Session, policy: FraudPolicy) -> None:
        self.session = session
        self.policy = policy

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        request_hash = fingerprint(request.model_dump(mode="json"))
        existing = self.session.get(FraudEvaluation, request.operation_id)
        if existing is not None:
            return self._replay(existing, request_hash)

        result = evaluate(
            Operation(
                operation_type=request.operation_type.value,
                amount=request.amount,
                currency=request.currency,
                source_account_id=request.source_account_id,
                destination_account_id=request.destination_account_id,
                payee=request.payee,
            ),
            self.policy,
        )
        record = FraudEvaluation(
            operation_id=request.operation_id,
            request_hash=request_hash,
            operation_type=request.operation_type.value,
            customer_id=request.customer_id,
            amount=str(request.amount),
            currency=request.currency,
            decision=result.decision.value,
            reason_codes=",".join(result.reason_codes),
            explanation=result.explanation,
            rules_version=RULES_VERSION,
        )
        self.session.add(record)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.get(FraudEvaluation, request.operation_id)
            if existing is None:
                raise
            return self._replay(existing, request_hash)
        # Only decision metadata is logged: no customer, account or payee data.
        logger.info(
            "fraud decision",
            extra={
                "operation_id": record.operation_id,
                "operation_type": record.operation_type,
                "decision": record.decision,
                "reason_codes": record.reason_codes,
            },
        )
        return self._response(record, replayed=False)

    def _replay(self, record: FraudEvaluation, request_hash: str) -> EvaluationResponse:
        if record.request_hash != request_hash:
            raise ConflictError(
                "Operation ID was already evaluated with a different payload",
                code="OPERATION_ID_CONFLICT",
            )
        return self._response(record, replayed=True)

    @staticmethod
    def _response(record: FraudEvaluation, *, replayed: bool) -> EvaluationResponse:
        return EvaluationResponse(
            operation_id=record.operation_id,
            decision=Decision(record.decision),
            reason_codes=record.reason_codes.split(","),
            explanation=record.explanation,
            rules_version=record.rules_version,
            evaluated_at=record.created_at,
            replayed=replayed,
        )

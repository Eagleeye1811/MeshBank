"""Typed clients for the services the transfer workflow depends on.

The workflow only depends on these small interfaces, so tests can substitute
in-memory fakes and later phases can change transport (sidecar, mTLS) without
touching business logic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from common.errors import DownstreamError, ServiceError
from common.http_client import InternalClient, error_code_of


class AccountInfo(BaseModel):
    id: str
    customer_id: str
    currency: str
    status: str


class OperationOutcome(BaseModel):
    operation_id: str
    status: str  # APPLIED | REJECTED
    rejection_code: str | None = None

    @property
    def applied(self) -> bool:
        return self.status == "APPLIED"


class FraudResult(BaseModel):
    decision: str
    reason_codes: list[str]
    explanation: str


class AccountGateway(Protocol):
    def get_account(self, account_id: str) -> AccountInfo | None: ...
    def debit(
        self, account_id: str, operation_id: str, amount: Decimal, currency: str, reference: str
    ) -> OperationOutcome: ...
    def credit(
        self, account_id: str, operation_id: str, amount: Decimal, currency: str, reference: str
    ) -> OperationOutcome: ...
    def reverse(
        self, account_id: str, operation_id: str, original_operation_id: str, reference: str
    ) -> OperationOutcome: ...


class FraudGateway(Protocol):
    def evaluate(self, payload: dict) -> FraudResult: ...


class NotificationGateway(Protocol):
    def notify(self, *, event_key: str, customer_id: str, type: str, message: str) -> None: ...


class HttpAccountClient:
    def __init__(self, http: InternalClient) -> None:
        self.http = http

    def get_account(self, account_id: str) -> AccountInfo | None:
        response = self.http.request("GET", f"/internal/accounts/{account_id}", retry_safe=True)
        if response.status_code == 404:
            return None
        self._raise_for_unexpected(response)
        return self.http.parse(response, AccountInfo)

    def debit(self, account_id, operation_id, amount, currency, reference) -> OperationOutcome:
        return self._operation(account_id, "debit", operation_id, amount, currency, reference)

    def credit(self, account_id, operation_id, amount, currency, reference) -> OperationOutcome:
        return self._operation(account_id, "credit", operation_id, amount, currency, reference)

    def reverse(
        self, account_id, operation_id, original_operation_id, reference
    ) -> OperationOutcome:
        response = self.http.request(
            "POST",
            f"/internal/accounts/{account_id}/reversals",
            json={
                "operation_id": operation_id,
                "original_operation_id": original_operation_id,
                "reference": reference,
            },
            retry_safe=True,  # idempotent by operation_id
        )
        self._raise_for_unexpected(response)
        return self.http.parse(response, OperationOutcome)

    def _operation(self, account_id, kind, operation_id, amount, currency, reference):
        response = self.http.request(
            "POST",
            f"/internal/accounts/{account_id}/{kind}",
            json={
                "operation_id": operation_id,
                "amount": str(amount),
                "currency": currency,
                "reference": reference,
            },
            retry_safe=True,  # idempotent by operation_id
        )
        if response.status_code == 404:
            return OperationOutcome(
                operation_id=operation_id, status="REJECTED", rejection_code="ACCOUNT_NOT_FOUND"
            )
        self._raise_for_unexpected(response)
        return self.http.parse(response, OperationOutcome)

    def _raise_for_unexpected(self, response) -> None:
        if response.status_code >= 400:
            raise ServiceError(
                "The account service rejected an internal request",
                code=error_code_of(response) or "ACCOUNT_SERVICE_REQUEST_REJECTED",
                status_code=502,
            )


class HttpFraudClient:
    def __init__(self, http: InternalClient) -> None:
        self.http = http

    def evaluate(self, payload: dict) -> FraudResult:
        response = self.http.request("POST", "/evaluate", json=payload, retry_safe=True)
        if response.status_code != 200:
            raise DownstreamError(
                "The fraud-service rejected the evaluation request",
                code="FRAUD_SERVICE_ERROR",
                status_code=502,
            )
        return self.http.parse(response, FraudResult)


class HttpNotificationClient:
    def __init__(self, http: InternalClient) -> None:
        self.http = http

    def notify(self, *, event_key: str, customer_id: str, type: str, message: str) -> None:
        response = self.http.request(
            "POST",
            "/notifications",
            json={
                "event_key": event_key,
                "customer_id": customer_id,
                "type": type,
                "message": message,
            },
            retry_safe=True,  # deduplicated by event_key
        )
        if response.status_code not in (200, 201):
            raise DownstreamError(
                "The notification-service rejected the notification",
                code="NOTIFICATION_SERVICE_ERROR",
                status_code=502,
            )

"""Typed clients for the services the payment workflow depends on.

Kept local to this service (rather than shared) so each service owns its
view of the contracts it consumes.
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


class FraudResult(BaseModel):
    decision: str
    reason_codes: list[str]
    explanation: str


class DebitTransaction(BaseModel):
    id: str
    status: str
    step: str
    failure_code: str | None = None
    failure_reason: str | None = None


class AccountGateway(Protocol):
    def get_account(self, account_id: str) -> AccountInfo | None: ...


class FraudGateway(Protocol):
    def evaluate(self, payload: dict) -> FraudResult: ...


class TransactionGateway(Protocol):
    def payment_debit(
        self,
        *,
        payment_id: str,
        customer_id: str,
        account_id: str,
        amount: Decimal,
        currency: str,
        payee: str,
    ) -> DebitTransaction: ...
    def reverse(self, transaction_id: str, reason: str) -> DebitTransaction: ...


class NotificationGateway(Protocol):
    def notify(self, *, event_key: str, customer_id: str, type: str, message: str) -> None: ...


class HttpAccountClient:
    def __init__(self, http: InternalClient) -> None:
        self.http = http

    def get_account(self, account_id: str) -> AccountInfo | None:
        response = self.http.request("GET", f"/internal/accounts/{account_id}", retry_safe=True)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DownstreamError(
                "Unexpected account-service response", code="ACCOUNT_SERVICE_ERROR", status_code=502
            )
        return self.http.parse(response, AccountInfo)


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


class HttpTransactionClient:
    def __init__(self, http: InternalClient) -> None:
        self.http = http

    def payment_debit(self, *, payment_id, customer_id, account_id, amount, currency, payee):
        response = self.http.request(
            "POST",
            "/internal/payment-debits",
            json={
                "payment_id": payment_id,
                "customer_id": customer_id,
                "account_id": account_id,
                "amount": str(amount),
                "currency": currency,
                "payee": payee,
            },
            retry_safe=True,  # idempotent per payment_id
        )
        return self._result(response)

    def reverse(self, transaction_id: str, reason: str) -> DebitTransaction:
        response = self.http.request(
            "POST",
            f"/internal/transactions/{transaction_id}/reversal",
            json={"reason": reason},
            retry_safe=True,  # reversing twice is a no-op
        )
        return self._result(response)

    def _result(self, response) -> DebitTransaction:
        if response.status_code in (200, 201):
            return self.http.parse(response, DebitTransaction)
        # 4xx: the request was refused before any money moved.
        raise ServiceError(
            "The transaction service refused the request",
            code=error_code_of(response) or "TRANSACTION_REFUSED",
            status_code=response.status_code,
        )


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
            retry_safe=True,
        )
        if response.status_code not in (200, 201):
            raise DownstreamError(
                "The notification-service rejected the notification",
                code="NOTIFICATION_SERVICE_ERROR",
                status_code=502,
            )

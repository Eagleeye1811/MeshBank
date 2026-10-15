"""In-memory fakes of downstream services with failure injection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from app.clients import AccountInfo, FraudResult, OperationOutcome
from common.errors import DownstreamError


def unavailable(target="account-service") -> DownstreamError:
    prefix = target.upper().replace("-", "_")
    return DownstreamError(
        f"{target} unavailable", code=f"{prefix}_UNAVAILABLE", maybe_processed=False
    )


def timeout(target="account-service") -> DownstreamError:
    prefix = target.upper().replace("-", "_")
    return DownstreamError(
        f"{target} timeout", code=f"{prefix}_TIMEOUT", status_code=504, maybe_processed=True
    )


@dataclass
class FakeAccount:
    customer_id: str
    balance: Decimal
    currency: str = "INR"
    status: str = "ACTIVE"


@dataclass
class FakeAccounts:
    accounts: dict[str, FakeAccount] = field(default_factory=dict)
    ops: dict[str, OperationOutcome] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    # method -> queue of errors to raise *before* applying
    fail_before: dict[str, list[Exception]] = field(default_factory=lambda: defaultdict(list))
    # method -> number of times to apply the change but lose the response
    lose_response: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    down: bool = False

    def add(self, account_id: str, customer_id: str, balance: str = "0.00", **kw) -> None:
        self.accounts[account_id] = FakeAccount(customer_id, Decimal(balance), **kw)

    def balance(self, account_id: str) -> Decimal:
        return self.accounts[account_id].balance

    def get_account(self, account_id: str) -> AccountInfo | None:
        if self.down:
            raise unavailable()
        acc = self.accounts.get(account_id)
        if acc is None:
            return None
        return AccountInfo(
            id=account_id, customer_id=acc.customer_id, currency=acc.currency, status=acc.status
        )

    def debit(self, account_id, operation_id, amount, currency, reference):
        return self._op("debit", account_id, operation_id, -amount)

    def credit(self, account_id, operation_id, amount, currency, reference):
        return self._op("credit", account_id, operation_id, amount)

    def reverse(self, account_id, operation_id, original_operation_id, reference):
        def apply():
            original = self.ops[original_operation_id]
            assert original.applied
            self.accounts[account_id].balance += self._amounts[original_operation_id]
            return OperationOutcome(operation_id=operation_id, status="APPLIED")

        return self._run("reverse", operation_id, apply)

    _amounts: dict[str, Decimal] = field(default_factory=dict)

    def _op(self, method, account_id, operation_id, delta: Decimal):
        def apply():
            acc = self.accounts.get(account_id)
            if acc is None:
                return OperationOutcome(
                    operation_id=operation_id, status="REJECTED", rejection_code="ACCOUNT_NOT_FOUND"
                )
            if acc.status != "ACTIVE":
                return OperationOutcome(
                    operation_id=operation_id, status="REJECTED", rejection_code="ACCOUNT_INACTIVE"
                )
            if acc.balance + delta < 0:
                return OperationOutcome(
                    operation_id=operation_id,
                    status="REJECTED",
                    rejection_code="INSUFFICIENT_FUNDS",
                )
            acc.balance += delta
            self._amounts[operation_id] = -delta
            return OperationOutcome(operation_id=operation_id, status="APPLIED")

        return self._run(method, operation_id, apply)

    def _run(self, method, operation_id, apply):
        self.calls.append((method, operation_id))
        if self.down:
            raise unavailable()
        if self.fail_before[method]:
            raise self.fail_before[method].pop(0)
        if operation_id not in self.ops:  # idempotent, like the real service
            self.ops[operation_id] = apply()
        if self.lose_response[method] > 0:
            self.lose_response[method] -= 1
            raise timeout()
        return self.ops[operation_id]

    def applied_count(self, method: str) -> int:
        return sum(
            1
            for op_id, outcome in self.ops.items()
            if op_id.endswith(f":{method}") and outcome.applied
        )


@dataclass
class FakeFraud:
    decision: str = "APPROVE"
    down: bool = False
    calls: list[dict] = field(default_factory=list)

    def evaluate(self, payload: dict) -> FraudResult:
        self.calls.append(payload)
        if self.down:
            raise unavailable("fraud-service")
        return FraudResult(
            decision=self.decision,
            reason_codes=["TEST_RULE"],
            explanation=f"test decision {self.decision}",
        )


@dataclass
class FakeNotifications:
    down: bool = False
    sent: dict[str, dict] = field(default_factory=dict)

    def notify(self, *, event_key, customer_id, type, message) -> None:
        if self.down:
            raise unavailable("notification-service")
        self.sent[event_key] = {"customer_id": customer_id, "type": type, "message": message}

"""Deterministic, transparent fraud rules.

These are simple configurable rules for a simulation. They are NOT machine
learning and NOT production-grade fraud detection. Every rule that fires adds
a reason code; the final decision is the most severe outcome.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal

from common.money import SUPPORTED_CURRENCIES, has_valid_precision

RULES_VERSION = "2026-10-phase1"


class Decision(enum.StrEnum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


_SEVERITY = {Decision.APPROVE: 0, Decision.REVIEW: 1, Decision.REJECT: 2}

_EXPLANATIONS = {
    "UNSUPPORTED_CURRENCY": "The currency is not supported",
    "NON_POSITIVE_AMOUNT": "The amount must be greater than zero",
    "INVALID_PRECISION": "The amount has more decimal places than the currency allows",
    "SAME_ACCOUNT": "Source and destination accounts are identical",
    "BLOCKED_PAYEE": "The payee is on the blocked list",
    "HARD_LIMIT_EXCEEDED": "The amount exceeds the hard limit",
    "REVIEW_THRESHOLD_EXCEEDED": "The amount exceeds the manual review threshold",
    "WITHIN_LIMITS": "No rule flagged this operation",
}


@dataclass(frozen=True)
class FraudPolicy:
    review_threshold: Decimal
    hard_limit: Decimal
    hard_limit_action: Decision
    blocked_payees: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Operation:
    operation_type: str
    amount: Decimal
    currency: str
    source_account_id: str
    destination_account_id: str | None = None
    payee: str | None = None


@dataclass(frozen=True)
class Evaluation:
    decision: Decision
    reason_codes: tuple[str, ...]
    explanation: str


def evaluate(op: Operation, policy: FraudPolicy) -> Evaluation:
    findings: list[tuple[Decision, str]] = []
    currency_ok = op.currency in SUPPORTED_CURRENCIES
    if not currency_ok:
        findings.append((Decision.REJECT, "UNSUPPORTED_CURRENCY"))
    if op.amount <= 0:
        findings.append((Decision.REJECT, "NON_POSITIVE_AMOUNT"))
    elif currency_ok and not has_valid_precision(op.amount, op.currency):
        findings.append((Decision.REJECT, "INVALID_PRECISION"))
    if op.destination_account_id and op.destination_account_id == op.source_account_id:
        findings.append((Decision.REJECT, "SAME_ACCOUNT"))
    if op.payee and op.payee.strip().lower() in policy.blocked_payees:
        findings.append((Decision.REJECT, "BLOCKED_PAYEE"))
    if op.amount > policy.hard_limit:
        findings.append((policy.hard_limit_action, "HARD_LIMIT_EXCEEDED"))
    elif op.amount > policy.review_threshold:
        findings.append((Decision.REVIEW, "REVIEW_THRESHOLD_EXCEEDED"))

    if not findings:
        return Evaluation(Decision.APPROVE, ("WITHIN_LIMITS",), _EXPLANATIONS["WITHIN_LIMITS"])
    decision = max((d for d, _ in findings), key=_SEVERITY.__getitem__)
    codes = tuple(code for _, code in findings)
    return Evaluation(decision, codes, "; ".join(_EXPLANATIONS[c] for c in codes))

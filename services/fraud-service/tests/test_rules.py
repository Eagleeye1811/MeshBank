from decimal import Decimal

import pytest

from app.rules import Decision, FraudPolicy, Operation, evaluate

POLICY = FraudPolicy(
    review_threshold=Decimal("50000.00"),
    hard_limit=Decimal("200000.00"),
    hard_limit_action=Decision.REJECT,
    blocked_payees=frozenset({"scam-co"}),
)


def op(amount: str, **kw) -> Operation:
    defaults = {
        "operation_type": "TRANSFER",
        "currency": "INR",
        "source_account_id": "a",
        "destination_account_id": "b",
    }
    defaults.update(kw)
    return Operation(amount=Decimal(amount), **defaults)


@pytest.mark.parametrize(
    ("amount", "decision", "code"),
    [
        ("0.01", Decision.APPROVE, "WITHIN_LIMITS"),
        ("50000.00", Decision.APPROVE, "WITHIN_LIMITS"),  # boundary: not strictly above
        ("50000.01", Decision.REVIEW, "REVIEW_THRESHOLD_EXCEEDED"),
        ("200000.00", Decision.REVIEW, "REVIEW_THRESHOLD_EXCEEDED"),
        ("200000.01", Decision.REJECT, "HARD_LIMIT_EXCEEDED"),
        ("0", Decision.REJECT, "NON_POSITIVE_AMOUNT"),
        ("-10", Decision.REJECT, "NON_POSITIVE_AMOUNT"),
        ("10.001", Decision.REJECT, "INVALID_PRECISION"),
    ],
)
def test_amount_rules_and_boundaries(amount, decision, code):
    result = evaluate(op(amount), POLICY)
    assert result.decision == decision
    assert code in result.reason_codes


def test_hard_limit_action_review_policy():
    policy = FraudPolicy(Decimal("50000"), Decimal("200000"), Decision.REVIEW)
    result = evaluate(op("250000.00"), policy)
    assert result.decision == Decision.REVIEW
    assert result.reason_codes == ("HARD_LIMIT_EXCEEDED",)


def test_unsupported_currency_same_account_and_blocked_payee():
    assert evaluate(op("10", currency="USD"), POLICY).decision == Decision.REJECT
    same = evaluate(op("10", destination_account_id="a"), POLICY)
    assert same.reason_codes == ("SAME_ACCOUNT",)
    blocked = evaluate(
        op("10", operation_type="PAYMENT", destination_account_id=None, payee=" SCAM-CO "), POLICY
    )
    assert blocked.decision == Decision.REJECT and blocked.reason_codes == ("BLOCKED_PAYEE",)


def test_most_severe_decision_wins_and_all_reasons_reported():
    result = evaluate(op("60000.00", payee="scam-co"), POLICY)
    assert result.decision == Decision.REJECT
    assert set(result.reason_codes) == {"BLOCKED_PAYEE", "REVIEW_THRESHOLD_EXCEEDED"}


def test_rules_are_deterministic():
    assert evaluate(op("75000"), POLICY) == evaluate(op("75000"), POLICY)

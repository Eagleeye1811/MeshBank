"""Deterministic simulated payment provider.

No real payment gateway is contacted. The outcome is a pure function of the
payment, so retries always produce the same answer and reference.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ProviderResult:
    approved: bool
    status: str  # APPROVED | DECLINED
    reference: str | None
    reason: str | None = None


class SimulatedProvider:
    def __init__(self, *, decline_prefix: str, max_amount: Decimal) -> None:
        self.decline_prefix = decline_prefix.upper()
        self.max_amount = max_amount

    def authorize(
        self, *, payment_id: str, payee_reference: str, amount: Decimal
    ) -> ProviderResult:
        if self.decline_prefix and payee_reference.upper().startswith(self.decline_prefix):
            return ProviderResult(False, "DECLINED", None, "Payee declined by simulated provider")
        if amount > self.max_amount:
            return ProviderResult(False, "DECLINED", None, "Amount above simulated provider limit")
        digest = hashlib.sha256(payment_id.encode()).hexdigest()[:16].upper()
        return ProviderResult(True, "APPROVED", f"SIMPAY-{digest}")

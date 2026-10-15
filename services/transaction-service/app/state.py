"""Explicit transaction state machine."""

from app.models import TransactionStatus as S

FINAL_STATUSES = frozenset({S.COMPLETED, S.REJECTED, S.FAILED, S.UNDER_REVIEW, S.REVERSED})

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.PROCESSING, S.REJECTED, S.FAILED}),
    S.PROCESSING: frozenset({S.PROCESSING, S.COMPLETED, S.REJECTED, S.FAILED, S.UNDER_REVIEW}),
    # Only a completed payment debit can later be refunded (provider decline).
    S.COMPLETED: frozenset({S.REVERSED}),
    S.REJECTED: frozenset(),
    S.FAILED: frozenset(),
    S.UNDER_REVIEW: frozenset(),
    S.REVERSED: frozenset(),
}


class InvalidTransition(RuntimeError):
    pass


def check_transition(current: S, target: S) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"Illegal transaction transition {current} -> {target}")

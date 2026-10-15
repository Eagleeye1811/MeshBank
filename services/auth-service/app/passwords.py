"""Password policy and Argon2id hashing."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def password_policy_violations(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"must be at most {MAX_PASSWORD_LENGTH} characters")
    if not any(c.isalpha() for c in password):
        problems.append("must contain a letter")
    if not any(c.isdigit() for c in password):
        problems.append("must contain a digit")
    if password.strip() != password:
        problems.append("must not start or end with whitespace")
    return problems


class PasswordService:
    def __init__(self, *, time_cost: int, memory_cost_kib: int, parallelism: int) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            type=Type.ID,
        )
        # Verified against when an email is unknown, so response timing does
        # not reveal whether an account exists.
        self._dummy_hash = self._hasher.hash("meshbank-dummy-password-for-timing")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash or self._dummy_hash, password) and (
                password_hash is not None
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

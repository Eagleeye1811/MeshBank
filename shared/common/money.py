"""Monetary amounts: Decimal only, explicit precision per currency.

Binary floating point is never used for money. Amounts are validated against
the currency's minor-unit exponent (INR: 2 decimal places) and persisted as
integer minor units (paise) by :class:`common.database.MoneyType`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, PlainSerializer

SUPPORTED_CURRENCIES: dict[str, int] = {"INR": 2}
DEFAULT_CURRENCY = "INR"
# Upper bound on any single amount (10^13 - 0.01). Keeps values within BIGINT.
MAX_AMOUNT = Decimal("9999999999999.99")


class MoneyError(ValueError):
    pass


def currency_exponent(currency: str) -> int:
    try:
        return SUPPORTED_CURRENCIES[currency]
    except KeyError as exc:
        raise MoneyError(f"Unsupported currency '{currency}'") from exc


def to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise MoneyError("Amount must be a decimal number")
    elif isinstance(value, int | str):
        try:
            result = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise MoneyError("Amount must be a decimal number") from exc
    elif isinstance(value, float):
        # JSON numbers may arrive as floats; use their shortest repr, which is
        # exact for the decimal literal the client wrote.
        result = Decimal(repr(value))
    else:
        raise MoneyError("Amount must be a decimal number")
    if not result.is_finite():
        raise MoneyError("Amount must be a finite number")
    return result


def has_valid_precision(amount: Decimal, currency: str = DEFAULT_CURRENCY) -> bool:
    quantum = Decimal(1).scaleb(-currency_exponent(currency))
    try:
        return amount == amount.quantize(quantum)
    except InvalidOperation:
        return False


def normalize_amount(amount: Decimal, currency: str = DEFAULT_CURRENCY) -> Decimal:
    """Return ``amount`` quantized to the currency's minor unit, or raise."""
    if not has_valid_precision(amount, currency):
        raise MoneyError(
            f"Amount has more than {currency_exponent(currency)} decimal places for {currency}"
        )
    return amount.quantize(Decimal(1).scaleb(-currency_exponent(currency)))


def validate_positive_amount(amount: Decimal, currency: str = DEFAULT_CURRENCY) -> Decimal:
    if amount <= 0:
        raise MoneyError("Amount must be greater than zero")
    if amount > MAX_AMOUNT:
        raise MoneyError("Amount exceeds the maximum supported value")
    return normalize_amount(amount, currency)


def to_minor_units(amount: Decimal, currency: str = DEFAULT_CURRENCY) -> int:
    normalized = normalize_amount(amount, currency)
    return int(normalized.scaleb(currency_exponent(currency)))


def from_minor_units(value: int, currency: str = DEFAULT_CURRENCY) -> Decimal:
    exp = currency_exponent(currency)
    return (Decimal(value).scaleb(-exp)).quantize(Decimal(1).scaleb(-exp))


def _validate_currency(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Currency must be a string")
    code = value.strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency; supported: {', '.join(SUPPORTED_CURRENCIES)}")
    return code


def _parse_amount(value: object) -> Decimal:
    try:
        return to_decimal(value)
    except MoneyError as exc:
        raise ValueError(str(exc)) from exc


def _positive(value: Decimal) -> Decimal:
    try:
        return validate_positive_amount(value)
    except MoneyError as exc:
        raise ValueError(str(exc)) from exc


_serialize_amount = PlainSerializer(lambda v: str(v), return_type=str, when_used="json")

# A currency code from SUPPORTED_CURRENCIES.
CurrencyCode = Annotated[str, BeforeValidator(_validate_currency)]
# Any finite decimal (used where a rule engine must see non-positive values).
DecimalAmount = Annotated[Decimal, BeforeValidator(_parse_amount), _serialize_amount]
# A strictly positive amount with valid INR precision (all customer requests).
PositiveAmount = Annotated[
    Decimal, BeforeValidator(_parse_amount), AfterValidator(_positive), _serialize_amount
]
# Serialisation-only alias for response models.
MoneyOut = Annotated[Decimal, _serialize_amount]

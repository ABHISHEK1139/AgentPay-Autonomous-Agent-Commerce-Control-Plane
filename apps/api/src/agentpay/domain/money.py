from __future__ import annotations

from dataclasses import dataclass
from string import ascii_uppercase
from typing import Final

from agentpay.domain.errors import (
    CurrencyMismatchError,
    InvalidCurrencyError,
    NegativeMoneyError,
    NonIntegerMoneyError,
)

_CURRENCY_CODE_LENGTH: Final = 3
_CURRENCY_CODE_CHARACTERS: Final = frozenset(ascii_uppercase)


@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount held as integer minor units in a single currency.

    Binary floating point is rejected outright: a float cannot represent most decimal
    fractions exactly, so allowing one here would let rounding decide financial outcomes.
    """

    minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise NonIntegerMoneyError(
                f"minor_units must be int, got {type(self.minor_units).__name__}"
            )
        if self.minor_units < 0:
            raise NegativeMoneyError(f"minor_units must not be negative, got {self.minor_units}")
        object.__setattr__(self, "currency", self._validated_currency(self.currency))

    @staticmethod
    def _validated_currency(currency: str) -> str:
        if not isinstance(currency, str):
            raise InvalidCurrencyError(f"currency must be str, got {type(currency).__name__}")
        normalized = currency.strip().upper()
        if len(normalized) != _CURRENCY_CODE_LENGTH:
            raise InvalidCurrencyError(
                f"currency must be {_CURRENCY_CODE_LENGTH} letters, got {normalized!r}"
            )
        if not set(normalized) <= _CURRENCY_CODE_CHARACTERS:
            raise InvalidCurrencyError(f"currency must be A-Z letters, got {normalized!r}")
        return normalized

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")

    def add(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def subtract(self, other: Money) -> Money:
        self._require_same_currency(other)
        result = self.minor_units - other.minor_units
        if result < 0:
            raise NegativeMoneyError(
                f"subtraction would produce a negative amount: {self.minor_units} - "
                f"{other.minor_units} = {result}"
            )
        return Money(result, self.currency)

    def multiply(self, quantity: int) -> Money:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise NonIntegerMoneyError(f"quantity must be int, got {type(quantity).__name__}")
        if quantity < 0:
            raise NegativeMoneyError(f"quantity must not be negative, got {quantity}")
        return Money(self.minor_units * quantity, self.currency)

    def is_greater_than(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units > other.minor_units

    def is_at_most(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units <= other.minor_units

    def __str__(self) -> str:
        return f"{self.minor_units} {self.currency}"


def total_money(amounts: tuple[Money, ...], currency: str) -> Money:
    """Sum amounts that must all share one currency, in a deterministic order."""
    result = Money.zero(currency)
    for amount in amounts:
        result = result.add(amount)
    return result

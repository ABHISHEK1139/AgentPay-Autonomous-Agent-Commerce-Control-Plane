from __future__ import annotations

from dataclasses import dataclass
from string import ascii_uppercase
from typing import Final

from agentpay.domain.currencies import CurrencyRegistry
from agentpay.domain.errors import (
    CurrencyMismatchError,
    DomainError,
    InvalidCurrencyError,
    InvalidMoneyError,
    NegativeMoneyError,
    NonIntegerMoneyError,
)

_CURRENCY_CODE_LENGTH: Final = 3
_CURRENCY_CODE_CHARACTERS: Final = frozenset(ascii_uppercase)


class InvalidExchangeRateError(DomainError):
    """Raised when an ExchangeRate definition or application is invalid."""

    code = "INVALID_EXCHANGE_RATE"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """Exact rational exchange rate from base_currency to quote_currency.

    The rate represents how many major quote currency units correspond to one major
    base currency unit, expressed as an exact rational number (rate_numerator / rate_denominator).
    """

    base_currency: str
    quote_currency: str
    rate_numerator: int
    rate_denominator: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", Money._validated_currency(self.base_currency))
        object.__setattr__(self, "quote_currency", Money._validated_currency(self.quote_currency))
        if isinstance(self.rate_numerator, bool) or not isinstance(self.rate_numerator, int):
            raise NonIntegerMoneyError(
                f"rate_numerator must be int, got {type(self.rate_numerator).__name__}"
            )
        if self.rate_numerator <= 0:
            raise InvalidExchangeRateError(
                f"rate_numerator must be positive, got {self.rate_numerator}"
            )
        if isinstance(self.rate_denominator, bool) or not isinstance(self.rate_denominator, int):
            raise NonIntegerMoneyError(
                f"rate_denominator must be int, got {type(self.rate_denominator).__name__}"
            )
        if self.rate_denominator <= 0:
            raise InvalidExchangeRateError(
                f"rate_denominator must be positive, got {self.rate_denominator}"
            )

    def inverse(self) -> ExchangeRate:
        """Return the exact inverse rate (quote -> base)."""
        return ExchangeRate(
            base_currency=self.quote_currency,
            quote_currency=self.base_currency,
            rate_numerator=self.rate_denominator,
            rate_denominator=self.rate_numerator,
        )


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
        if not CurrencyRegistry.is_supported(normalized):
            raise InvalidCurrencyError(f"unsupported currency code: {normalized!r}")
        return normalized

    @property
    def exponent(self) -> int:
        """Minor-unit exponent defined by ISO 4217 for this currency."""
        return CurrencyRegistry.get_exponent(self.currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, major: int, currency: str) -> Money:
        """Construct Money from major integer units (e.g. 15 USD -> 1500 minor units)."""
        if isinstance(major, bool) or not isinstance(major, int):
            raise NonIntegerMoneyError(f"major must be int, got {type(major).__name__}")
        if major < 0:
            raise NegativeMoneyError(f"major units must not be negative, got {major}")
        validated_curr = cls._validated_currency(currency)
        exp = CurrencyRegistry.get_exponent(validated_curr)
        return cls(major * (10**exp), validated_curr)

    @classmethod
    def from_decimal(cls, decimal_str: str, currency: str) -> Money:
        """Parse an integer or decimal string strictly into integer minor units without float.

        Fails closed on extra decimal places beyond the currency exponent, or non-zero decimal
        places for exponent-0 currencies.
        """
        if not isinstance(decimal_str, str):
            raise InvalidMoneyError(f"decimal_str must be str, got {type(decimal_str).__name__}")
        validated_curr = cls._validated_currency(currency)
        exp = CurrencyRegistry.get_exponent(validated_curr)

        s = decimal_str.strip()
        if not s:
            raise InvalidMoneyError("decimal string must not be empty")

        if s.startswith("-"):
            raise NegativeMoneyError(f"amount must not be negative: {s!r}")

        if s.startswith("+"):
            raise InvalidMoneyError(f"unexpected sign in decimal string: {s!r}")

        if "." in s:
            parts = s.split(".")
            if len(parts) != 2:
                raise InvalidMoneyError(f"malformed decimal string: {s!r}")
            major_str, frac_str = parts
        else:
            major_str, frac_str = s, ""

        if not major_str and not frac_str:
            raise InvalidMoneyError(f"malformed decimal string: {s!r}")

        if not major_str and frac_str:
            major_str = "0"

        if not major_str.isdigit():
            raise InvalidMoneyError(f"invalid characters in major part: {major_str!r}")
        if frac_str and not frac_str.isdigit():
            raise InvalidMoneyError(f"invalid characters in fractional part: {frac_str!r}")

        if exp == 0:
            if frac_str and int(frac_str) != 0:
                raise InvalidMoneyError(
                    f"currency {validated_curr} has exponent 0 and cannot have non-zero fractional units: {s!r}"
                )
            minor_units = int(major_str)
        else:
            if len(frac_str) > exp:
                raise InvalidMoneyError(
                    f"too many decimal places for {validated_curr} (max {exp}): {s!r}"
                )
            scaled_frac = frac_str.ljust(exp, "0")
            frac_units = int(scaled_frac) if scaled_frac else 0
            minor_units = int(major_str) * (10**exp) + frac_units

        return cls(minor_units, validated_curr)

    def to_decimal_str(self) -> str:
        """Format the monetary amount as an exact decimal string (e.g. '1499.00', '1500')."""
        exp = self.exponent
        if exp == 0:
            return str(self.minor_units)
        factor = 10**exp
        major = self.minor_units // factor
        fractional = self.minor_units % factor
        return f"{major}.{fractional:0{exp}d}"

    def format_amount(self) -> str:
        """Format the amount with currency (e.g. '1499.00 INR', '1500 JPY', '1.500 KWD')."""
        return f"{self.to_decimal_str()} {self.currency}"

    def convert(self, rate: ExchangeRate) -> Money:
        """Convert this amount to quote currency using exact rational scaling and round-half-up."""
        if self.currency != rate.base_currency:
            raise CurrencyMismatchError(
                f"exchange rate base currency {rate.base_currency} does not match money currency {self.currency}"
            )
        base_exp = self.exponent
        quote_exp = CurrencyRegistry.get_exponent(rate.quote_currency)
        scaled_num = self.minor_units * (10**quote_exp) * rate.rate_numerator
        scaled_denom = (10**base_exp) * rate.rate_denominator
        quote_minor = (scaled_num + scaled_denom // 2) // scaled_denom
        return Money(quote_minor, rate.quote_currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")

    def _resolve_operand(self, other: Money, rate: ExchangeRate | None) -> Money:
        if self.currency == other.currency:
            if rate is not None:
                if rate.base_currency != other.currency or rate.quote_currency != self.currency:
                    raise CurrencyMismatchError(
                        f"exchange rate {rate.base_currency}->{rate.quote_currency} does not match "
                        f"currencies {other.currency} and {self.currency}"
                    )
                return other.convert(rate)
            return other

        if rate is None:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} with {other.currency} without an explicit exchange rate"
            )

        if rate.base_currency != other.currency or rate.quote_currency != self.currency:
            raise CurrencyMismatchError(
                f"exchange rate must convert from {other.currency} to {self.currency}, "
                f"got {rate.base_currency} -> {rate.quote_currency}"
            )

        return other.convert(rate)

    def add(self, other: Money, rate: ExchangeRate | None = None) -> Money:
        resolved_other = self._resolve_operand(other, rate)
        return Money(self.minor_units + resolved_other.minor_units, self.currency)

    def subtract(self, other: Money, rate: ExchangeRate | None = None) -> Money:
        resolved_other = self._resolve_operand(other, rate)
        result = self.minor_units - resolved_other.minor_units
        if result < 0:
            raise NegativeMoneyError(
                f"subtraction would produce a negative amount: {self.minor_units} - "
                f"{resolved_other.minor_units} = {result}"
            )
        return Money(result, self.currency)

    def multiply(self, quantity: int) -> Money:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise NonIntegerMoneyError(f"quantity must be int, got {type(quantity).__name__}")
        if quantity < 0:
            raise NegativeMoneyError(f"quantity must not be negative, got {quantity}")
        return Money(self.minor_units * quantity, self.currency)

    def is_greater_than(self, other: Money, rate: ExchangeRate | None = None) -> bool:
        resolved_other = self._resolve_operand(other, rate)
        return self.minor_units > resolved_other.minor_units

    def is_at_most(self, other: Money, rate: ExchangeRate | None = None) -> bool:
        resolved_other = self._resolve_operand(other, rate)
        return self.minor_units <= resolved_other.minor_units

    def __str__(self) -> str:
        return f"{self.minor_units} {self.currency}"


def total_money(amounts: tuple[Money, ...], currency: str) -> Money:
    """Sum amounts that must all share one currency, in a deterministic order."""
    result = Money.zero(currency)
    for amount in amounts:
        result = result.add(amount)
    return result

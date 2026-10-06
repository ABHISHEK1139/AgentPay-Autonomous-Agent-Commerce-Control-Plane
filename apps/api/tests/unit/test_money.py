from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.domain.errors import (
    CurrencyMismatchError,
    InvalidCurrencyError,
    NegativeMoneyError,
    NonIntegerMoneyError,
)
from agentpay.domain.money import Money, total_money

CURRENCIES = st.sampled_from(["INR", "USD", "EUR", "JPY", "GBP"])
NON_NEGATIVE_MINOR = st.integers(min_value=0, max_value=10**15)


def test_zero_has_no_minor_units() -> None:
    assert Money.zero("inr") == Money(0, "INR")


def test_currency_is_normalized_to_upper_case() -> None:
    assert Money(149900, "inr").currency == "INR"


@pytest.mark.parametrize("currency", ["IN", "INRX", "I1R", "", "  ", "IN ", 42])
def test_invalid_currency_is_rejected(currency: object) -> None:
    with pytest.raises(InvalidCurrencyError):
        Money(100, currency)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1499.5, "149900", None, True, False, object()])
def test_non_integer_minor_units_are_rejected(value: object) -> None:
    with pytest.raises(NonIntegerMoneyError):
        Money(value, "INR")  # type: ignore[arg-type]


def test_negative_minor_units_are_rejected() -> None:
    with pytest.raises(NegativeMoneyError):
        Money(-1, "INR")


def test_add_requires_matching_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").add(Money(100, "USD"))


def test_add_is_exact_for_amounts_float_cannot_represent() -> None:
    assert 0.1 + 0.2 != 0.3
    assert Money(10, "INR").add(Money(20, "INR")).minor_units == 30


def test_subtraction_refuses_to_produce_negative_amounts() -> None:
    with pytest.raises(NegativeMoneyError):
        Money(100, "INR").subtract(Money(101, "INR"))


def test_subtract_to_zero_is_allowed() -> None:
    assert Money(100, "INR").subtract(Money(100, "INR")).minor_units == 0


def test_multiply_by_quantity() -> None:
    assert Money(1499, "INR").multiply(3).minor_units == 4497


@pytest.mark.parametrize("quantity", [-1, 1.5, "2", True])
def test_multiply_rejects_invalid_quantities(quantity: object) -> None:
    with pytest.raises((NegativeMoneyError, NonIntegerMoneyError)):
        Money(1499, "INR").multiply(quantity)  # type: ignore[arg-type]


def test_comparisons_require_matching_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").is_greater_than(Money(100, "USD"))
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").is_at_most(Money(100, "USD"))


def test_total_is_currency_safe() -> None:
    amounts = (Money(1499, "INR"), Money(2500, "INR"), Money(1, "INR"))
    assert total_money(amounts, "INR") == Money(4000, "INR")


def test_total_of_empty_tuple_is_zero() -> None:
    assert total_money((), "USD") == Money(0, "USD")


def test_money_is_immutable() -> None:
    amount = Money(100, "INR")
    with pytest.raises(FrozenInstanceError):
        amount.minor_units = 200  # type: ignore[misc]


@given(minor_units=NON_NEGATIVE_MINOR, currency=CURRENCIES)
def test_money_roundtrips_through_construction(minor_units: int, currency: str) -> None:
    amount = Money(minor_units, currency)
    assert amount.minor_units == minor_units
    assert amount.currency == currency


@given(
    left=NON_NEGATIVE_MINOR,
    right=NON_NEGATIVE_MINOR,
    currency=CURRENCIES,
)
def test_addition_is_commutative_and_associative(left: int, right: int, currency: str) -> None:
    a, b = Money(left, currency), Money(right, currency)
    assert a.add(b).minor_units == b.add(a).minor_units
    assert (
        a.add(b).add(Money(7, currency)).minor_units == a.add(b.add(Money(7, currency))).minor_units
    )


@given(
    major=st.integers(min_value=0, max_value=10**6),
    minor=st.integers(min_value=0, max_value=99),
    currency=CURRENCIES,
)
def test_decimal_minor_units_never_drift(major: int, minor: int, currency: str) -> None:
    scaled = Money(major * 100 + minor, currency)
    assert scaled.minor_units == major * 100 + minor

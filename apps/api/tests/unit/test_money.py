from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.domain.currencies import (
    CurrencyInfo,
    CurrencyRegistry,
    get_exponent,
    is_supported,
    supported_currencies,
)
from agentpay.domain.currencies import (
    get as get_currency,
)
from agentpay.domain.errors import (
    CurrencyMismatchError,
    InvalidCurrencyError,
    InvalidMoneyError,
    NegativeMoneyError,
    NonIntegerMoneyError,
)
from agentpay.domain.money import (
    ExchangeRate,
    InvalidExchangeRateError,
    Money,
    total_money,
)

CURRENCIES = st.sampled_from(["INR", "USD", "EUR", "JPY", "GBP", "KWD", "CLF"])
NON_NEGATIVE_MINOR = st.integers(min_value=0, max_value=10**15)


# ---------------------------------------------------------------------------
# 1. ISO 4217 Currency Registry & Exponent Tests
# ---------------------------------------------------------------------------


def test_currency_registry_resolves_standard_exponents() -> None:
    # Exponent 0
    assert CurrencyRegistry.get_exponent("JPY") == 0
    assert CurrencyRegistry.get_exponent("KRW") == 0
    assert CurrencyRegistry.get_exponent("VND") == 0
    assert CurrencyRegistry.get_exponent("CLP") == 0

    # Exponent 2
    assert CurrencyRegistry.get_exponent("USD") == 2
    assert CurrencyRegistry.get_exponent("EUR") == 2
    assert CurrencyRegistry.get_exponent("GBP") == 2
    assert CurrencyRegistry.get_exponent("INR") == 2
    assert CurrencyRegistry.get_exponent("CAD") == 2
    assert CurrencyRegistry.get_exponent("XTS") == 2

    # Exponent 3
    assert CurrencyRegistry.get_exponent("KWD") == 3
    assert CurrencyRegistry.get_exponent("BHD") == 3
    assert CurrencyRegistry.get_exponent("OMR") == 3
    assert CurrencyRegistry.get_exponent("JOD") == 3

    # Exponent 4
    assert CurrencyRegistry.get_exponent("CLF") == 4
    assert CurrencyRegistry.get_exponent("UYW") == 4


def test_currency_registry_metadata_and_helpers() -> None:
    usd = CurrencyRegistry.get("USD")
    assert usd == CurrencyInfo(
        code="USD", exponent=2, numeric_code="840", name="United States Dollar"
    )
    assert get_currency("jpy").code == "JPY"
    assert get_exponent("kwd") == 3
    assert is_supported("inr") is True
    assert is_supported("ZZZ") is False
    assert is_supported(123) is False

    all_currencies = supported_currencies()
    assert isinstance(all_currencies, tuple)
    assert "USD" in all_currencies
    assert "JPY" in all_currencies
    assert "KWD" in all_currencies
    assert "CLF" in all_currencies


def test_currency_registry_rejects_unsupported_or_invalid_codes() -> None:
    with pytest.raises(InvalidCurrencyError):
        CurrencyRegistry.get("ZZZ")
    with pytest.raises(InvalidCurrencyError):
        CurrencyRegistry.get("FOO")
    with pytest.raises(InvalidCurrencyError):
        CurrencyRegistry.get(42)  # type: ignore[arg-type]


def test_money_exponent_property() -> None:
    assert Money(100, "USD").exponent == 2
    assert Money(100, "JPY").exponent == 0
    assert Money(100, "KWD").exponent == 3
    assert Money(100, "CLF").exponent == 4


# ---------------------------------------------------------------------------
# 2. Construction, Immutability & Validation Tests
# ---------------------------------------------------------------------------


def test_zero_has_no_minor_units() -> None:
    assert Money.zero("inr") == Money(0, "INR")


def test_currency_is_normalized_to_upper_case() -> None:
    assert Money(149900, "inr").currency == "INR"


@pytest.mark.parametrize("currency", ["IN", "INRX", "I1R", "", "  ", "IN ", "ZZZ", 42])
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


def test_money_is_immutable() -> None:
    amount = Money(100, "INR")
    with pytest.raises(FrozenInstanceError):
        amount.minor_units = 200  # type: ignore[misc]


def test_backward_compatible_str_representation() -> None:
    assert str(Money(149900, "INR")) == "149900 INR"
    assert str(Money(1500, "JPY")) == "1500 JPY"
    assert str(Money(1500, "KWD")) == "1500 KWD"


# ---------------------------------------------------------------------------
# 3. Decimal Formatting & Major Units
# ---------------------------------------------------------------------------


def test_to_decimal_str_and_format_amount() -> None:
    # 0 decimal places (JPY)
    jpy = Money(1500, "JPY")
    assert jpy.to_decimal_str() == "1500"
    assert jpy.format_amount() == "1500 JPY"
    assert Money(0, "JPY").to_decimal_str() == "0"

    # 2 decimal places (INR, USD)
    inr = Money(149900, "INR")
    assert inr.to_decimal_str() == "1499.00"
    assert inr.format_amount() == "1499.00 INR"

    usd_small = Money(5, "USD")
    assert usd_small.to_decimal_str() == "0.05"
    assert usd_small.format_amount() == "0.05 USD"
    assert Money(0, "USD").to_decimal_str() == "0.00"

    # 3 decimal places (KWD)
    kwd = Money(1500, "KWD")
    assert kwd.to_decimal_str() == "1.500"
    assert kwd.format_amount() == "1.500 KWD"

    kwd_small = Money(1, "KWD")
    assert kwd_small.to_decimal_str() == "0.001"
    assert kwd_small.format_amount() == "0.001 KWD"

    # 4 decimal places (CLF)
    clf = Money(123456, "CLF")
    assert clf.to_decimal_str() == "12.3456"
    assert clf.format_amount() == "12.3456 CLF"


def test_from_major_construction() -> None:
    assert Money.from_major(15, "USD") == Money(1500, "USD")
    assert Money.from_major(1500, "JPY") == Money(1500, "JPY")
    assert Money.from_major(2, "KWD") == Money(2000, "KWD")
    assert Money.from_major(3, "CLF") == Money(30000, "CLF")
    assert Money.from_major(0, "EUR") == Money(0, "EUR")

    with pytest.raises(NegativeMoneyError):
        Money.from_major(-5, "USD")
    with pytest.raises(NonIntegerMoneyError):
        Money.from_major(10.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(NonIntegerMoneyError):
        Money.from_major(True, "USD")


# ---------------------------------------------------------------------------
# 4. Decimal Parsing (from_decimal)
# ---------------------------------------------------------------------------


def test_from_decimal_valid_inputs() -> None:
    assert Money.from_decimal("1499.00", "INR") == Money(149900, "INR")
    assert Money.from_decimal("14.99", "USD") == Money(1499, "USD")
    assert Money.from_decimal("14.9", "USD") == Money(1490, "USD")
    assert Money.from_decimal("14", "USD") == Money(1400, "USD")
    assert Money.from_decimal(".50", "USD") == Money(50, "USD")
    assert Money.from_decimal("0.05", "USD") == Money(5, "USD")
    assert Money.from_decimal("0", "USD") == Money(0, "USD")
    assert Money.from_decimal("0.00", "USD") == Money(0, "USD")

    # Exponent 0
    assert Money.from_decimal("1500", "JPY") == Money(1500, "JPY")
    assert Money.from_decimal("1500.00", "JPY") == Money(1500, "JPY")

    # Exponent 3
    assert Money.from_decimal("1.500", "KWD") == Money(1500, "KWD")
    assert Money.from_decimal("1.5", "KWD") == Money(1500, "KWD")
    assert Money.from_decimal("0.001", "KWD") == Money(1, "KWD")

    # Exponent 4
    assert Money.from_decimal("12.3456", "CLF") == Money(123456, "CLF")


def test_from_decimal_rejects_extra_precision_or_malformed_input() -> None:
    # Too many decimal places fails closed
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("14.999", "USD")  # 3 decimals for 2-decimal currency
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("1.5001", "KWD")  # 4 decimals for 3-decimal currency

    # Exponent 0 rejects non-zero fractional values
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("1500.5", "JPY")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("100.1", "KRW")

    # Rejection of negatives and malformed strings
    with pytest.raises(NegativeMoneyError):
        Money.from_decimal("-14.99", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("   ", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal(".", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("14.99.1", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("abc", "USD")
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("+14.99", "USD")

    # Rejection of floats and booleans
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal(14.99, "USD")  # type: ignore[arg-type]
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal(True, "USD")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Exchange Rates & Cross-Currency Conversions
# ---------------------------------------------------------------------------


def test_exchange_rate_validation() -> None:
    rate = ExchangeRate("USD", "JPY", 150)
    assert rate.base_currency == "USD"
    assert rate.quote_currency == "JPY"
    assert rate.rate_numerator == 150
    assert rate.rate_denominator == 1

    inv = rate.inverse()
    assert inv.base_currency == "JPY"
    assert inv.quote_currency == "USD"
    assert inv.rate_numerator == 1
    assert inv.rate_denominator == 150

    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", 0)
    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", -10)
    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", 150, 0)
    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", 150, -1)
    with pytest.raises(NonIntegerMoneyError):
        ExchangeRate("USD", "JPY", True)
    with pytest.raises(NonIntegerMoneyError):
        ExchangeRate("USD", "JPY", 150.5)  # type: ignore[arg-type]


def test_money_convert_between_different_exponent_classes() -> None:
    # USD (exp 2) -> JPY (exp 0) at 150 JPY per 1 USD
    usd_amount = Money(10000, "USD")  # 100.00 USD
    rate_usd_to_jpy = ExchangeRate("USD", "JPY", 150)
    jpy_converted = usd_amount.convert(rate_usd_to_jpy)
    assert jpy_converted == Money(15000, "JPY")

    # JPY (exp 0) -> USD (exp 2) at 1/150 USD per 1 JPY
    rate_jpy_to_usd = rate_usd_to_jpy.inverse()
    usd_converted = jpy_converted.convert(rate_jpy_to_usd)
    assert usd_converted == Money(10000, "USD")

    # USD (exp 2) -> KWD (exp 3) at 31/100 KWD per 1 USD (0.310 KWD per USD)
    rate_usd_to_kwd = ExchangeRate("USD", "KWD", 31, 100)
    kwd_converted = usd_amount.convert(rate_usd_to_kwd)
    assert kwd_converted == Money(31000, "KWD")  # 31.000 KWD


def test_money_convert_round_half_up() -> None:
    # Rate 1/2: 1 minor unit of USD converts to 0.5 minor unit of USD -> rounds up to 1
    half_rate = ExchangeRate("USD", "USD", 1, 2)
    assert Money(1, "USD").convert(half_rate) == Money(1, "USD")
    assert Money(2, "USD").convert(half_rate) == Money(1, "USD")
    assert Money(3, "USD").convert(half_rate) == Money(2, "USD")

    # Round down below 0.5:
    quarter_rate = ExchangeRate("USD", "USD", 1, 4)  # 0.25
    assert Money(1, "USD").convert(quarter_rate) == Money(0, "USD")  # 0.25 -> 0
    assert Money(2, "USD").convert(quarter_rate) == Money(1, "USD")  # 0.50 -> 1
    assert Money(3, "USD").convert(quarter_rate) == Money(1, "USD")  # 0.75 -> 1


def test_money_convert_fails_closed_on_base_mismatch() -> None:
    rate = ExchangeRate("EUR", "JPY", 160)
    with pytest.raises(CurrencyMismatchError):
        Money(1000, "USD").convert(rate)


# ---------------------------------------------------------------------------
# 6. Cross-Currency Arithmetic & Comparisons
# ---------------------------------------------------------------------------


def test_add_requires_matching_currency_or_valid_rate() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").add(Money(100, "USD"))

    # JPY (exp 0) to USD (exp 2): 15000 JPY at 1/150 USD/JPY = 10000 minor units (100.00 USD)
    rate = ExchangeRate("JPY", "USD", 1, 150)
    usd = Money(1000, "USD")  # 10.00 USD
    jpy = Money(15000, "JPY")  # 100.00 USD
    result = usd.add(jpy, rate=rate)
    assert result == Money(11000, "USD")  # 110.00 USD


def test_add_rejects_rate_with_wrong_currency_direction() -> None:
    wrong_rate = ExchangeRate("USD", "JPY", 150)  # wrong direction: should be JPY -> USD
    with pytest.raises(CurrencyMismatchError):
        Money(1000, "USD").add(Money(15000, "JPY"), rate=wrong_rate)


def test_add_is_exact_for_amounts_float_cannot_represent() -> None:
    assert 0.1 + 0.2 != 0.3
    assert Money(10, "INR").add(Money(20, "INR")).minor_units == 30


def test_subtraction_refuses_to_produce_negative_amounts() -> None:
    with pytest.raises(NegativeMoneyError):
        Money(100, "INR").subtract(Money(101, "INR"))

    rate = ExchangeRate("JPY", "USD", 1, 150)
    with pytest.raises(NegativeMoneyError):
        Money(5000, "USD").subtract(Money(15000, "JPY"), rate=rate)  # 50 - 100 < 0


def test_subtract_to_zero_is_allowed() -> None:
    assert Money(100, "INR").subtract(Money(100, "INR")).minor_units == 0


def test_subtract_with_rate() -> None:
    rate = ExchangeRate("JPY", "USD", 1, 150)
    usd = Money(15000, "USD")  # 150.00 USD
    jpy = Money(15000, "JPY")  # 100.00 USD
    assert usd.subtract(jpy, rate=rate) == Money(5000, "USD")  # 50.00 USD


def test_multiply_by_quantity() -> None:
    assert Money(1499, "INR").multiply(3).minor_units == 4497


@pytest.mark.parametrize("quantity", [-1, 1.5, "2", True])
def test_multiply_rejects_invalid_quantities(quantity: object) -> None:
    with pytest.raises((NegativeMoneyError, NonIntegerMoneyError)):
        Money(1499, "INR").multiply(quantity)  # type: ignore[arg-type]


def test_comparisons_require_matching_currency_or_valid_rate() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").is_greater_than(Money(100, "USD"))
    with pytest.raises(CurrencyMismatchError):
        Money(100, "INR").is_at_most(Money(100, "USD"))

    rate = ExchangeRate("JPY", "USD", 1, 150)
    usd_high = Money(15000, "USD")  # 150.00 USD
    usd_low = Money(5000, "USD")  # 50.00 USD
    jpy = Money(15000, "JPY")  # 100.00 USD

    assert usd_high.is_greater_than(jpy, rate=rate) is True
    assert usd_low.is_greater_than(jpy, rate=rate) is False
    assert usd_high.is_at_most(jpy, rate=rate) is False
    assert usd_low.is_at_most(jpy, rate=rate) is True


def test_total_is_currency_safe() -> None:
    amounts = (Money(1499, "INR"), Money(2500, "INR"), Money(1, "INR"))
    assert total_money(amounts, "INR") == Money(4000, "INR")


def test_total_of_empty_tuple_is_zero() -> None:
    assert total_money((), "USD") == Money(0, "USD")


def test_total_fails_closed_on_mismatched_currencies() -> None:
    amounts = (Money(100, "USD"), Money(100, "EUR"))
    with pytest.raises(CurrencyMismatchError):
        total_money(amounts, "USD")


# ---------------------------------------------------------------------------
# 7. Hypothesis Property-Based Invariant Tests
# ---------------------------------------------------------------------------


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


@given(minor_units=st.integers(min_value=0, max_value=10**9), currency=CURRENCIES)
def test_money_decimal_string_roundtrips_losslessly(minor_units: int, currency: str) -> None:
    original = Money(minor_units, currency)
    decimal_str = original.to_decimal_str()
    recovered = Money.from_decimal(decimal_str, currency)
    assert recovered == original


@given(major=st.integers(min_value=0, max_value=10**9), currency=CURRENCIES)
def test_money_from_major_scales_strictly_by_exponent(major: int, currency: str) -> None:
    m = Money.from_major(major, currency)
    exp = CurrencyRegistry.get_exponent(currency)
    assert m.minor_units == major * (10**exp)
    assert m.currency == currency


@given(minor_units=st.integers(min_value=0, max_value=10**9), currency=CURRENCIES)
def test_identity_exchange_rate_preserves_exact_amount(minor_units: int, currency: str) -> None:
    original = Money(minor_units, currency)
    identity_rate = ExchangeRate(currency, currency, 1, 1)
    converted = original.convert(identity_rate)
    assert converted == original

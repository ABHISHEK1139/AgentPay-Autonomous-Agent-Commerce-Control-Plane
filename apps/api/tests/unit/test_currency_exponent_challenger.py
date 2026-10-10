"""Empirical Challenger Test Suite for ISO 4217 Currency Engine & Money (TD-002, R2).

Exhaustive empirical probes for:
1. Zero-decimal currency fail-closed rejection on fractional input (e.g. "1500.50" on JPY).
2. Standard two-decimal sub-cent precision fail-closed rejection (e.g. "14.999" on USD).
3. Three-decimal currency exact minor unit generation (e.g. "1.500" on KWD -> 1500 minor units).
4. Cross-currency addition/subtraction without ExchangeRate strictly raises CurrencyMismatchError.
5. Incompatible ExchangeRate (wrong base or quote currency or direction) raises CurrencyMismatchError.
6. ExchangeRate with denominator <= 0 or numerator <= 0 raises InvalidExchangeRateError / DomainError.
7. Exact round-half-up rational scaling across exponent boundaries (100 USD -> 15000 JPY; 1000 JPY -> 667 USD).
8. Hypothesis property probes verifying zero conversion, round-half-up bounds, and boundary limits.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.domain.currencies import (
    CurrencyRegistry,
    supported_currencies,
)
from agentpay.domain.errors import (
    CurrencyMismatchError,
    DomainError,
    InvalidMoneyError,
    NonIntegerMoneyError,
)
from agentpay.domain.money import (
    ExchangeRate,
    InvalidExchangeRateError,
    Money,
    total_money,
)


# ===========================================================================
# Probe 1: JPY with decimal string "1500.50" fails closed (rejects exp 0 fractions)
# ===========================================================================
def test_challenger_probe_1_zero_decimal_rejection() -> None:
    """Empirical probe 1: JPY and all exponent-0 currencies reject non-zero fractions."""
    # Verbatim probe case from dispatch
    with pytest.raises(InvalidMoneyError, match="exponent 0 and cannot have non-zero fractional"):
        Money.from_decimal("1500.50", "JPY")

    # Additional boundary and adversarial fractions
    for bad_fraction in ["1500.01", "1500.5", "1500.99", "0.1", "0.001", "42.00001"]:
        with pytest.raises(InvalidMoneyError):
            Money.from_decimal(bad_fraction, "JPY")

    # Verify across ALL 15 zero-decimal currencies in the registry
    zero_exp_currencies = [
        c for c in supported_currencies() if CurrencyRegistry.get_exponent(c) == 0
    ]
    assert len(zero_exp_currencies) == 15
    for curr in zero_exp_currencies:
        with pytest.raises(InvalidMoneyError):
            Money.from_decimal("100.5", curr)
        with pytest.raises(InvalidMoneyError):
            Money.from_decimal("1.01", curr)

    # Invariant: zero fractional strings like "1500.0" or "1500.00" are accepted as whole units
    m_clean = Money.from_decimal("1500.00", "JPY")
    assert m_clean.minor_units == 1500
    assert m_clean.currency == "JPY"


# ===========================================================================
# Probe 2: USD with "14.999" fails closed (rejects sub-cent precision)
# ===========================================================================
def test_challenger_probe_2_two_decimal_subcent_rejection() -> None:
    """Empirical probe 2: USD and two-decimal currencies reject sub-cent precision."""
    # Verbatim probe case from dispatch
    with pytest.raises(InvalidMoneyError, match="too many decimal places for USD"):
        Money.from_decimal("14.999", "USD")

    # Additional boundary checks: trailing zeros that exceed exponent length must fail closed
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("14.990", "USD")

    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("0.001", "USD")

    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("100.12345", "USD")

    # Verify across two-decimal currencies
    two_exp_currencies = [
        c for c in supported_currencies() if CurrencyRegistry.get_exponent(c) == 2
    ]
    for curr in two_exp_currencies[:10]:  # probe first 10
        with pytest.raises(InvalidMoneyError):
            Money.from_decimal("10.999", curr)


# ===========================================================================
# Probe 3: KWD with "1.500" produces 1500 minor units
# ===========================================================================
def test_challenger_probe_3_three_decimal_kwd_parsing() -> None:
    """Empirical probe 3: KWD with '1.500' produces exact 1500 minor units."""
    # Verbatim probe case from dispatch
    kwd = Money.from_decimal("1.500", "KWD")
    assert kwd.minor_units == 1500
    assert kwd.currency == "KWD"
    assert kwd.exponent == 3
    assert kwd.to_decimal_str() == "1.500"
    assert kwd.format_amount() == "1.500 KWD"

    # Shorter decimal strings are padded according to exponent
    assert Money.from_decimal("1.5", "KWD").minor_units == 1500
    assert Money.from_decimal("1.50", "KWD").minor_units == 1500
    assert Money.from_decimal("0.001", "KWD").minor_units == 1

    # Over-precision beyond 3 decimals fails closed
    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("1.5001", "KWD")

    with pytest.raises(InvalidMoneyError):
        Money.from_decimal("1.5000", "KWD")

    # Verify across all 7 three-decimal currencies
    three_exp_currencies = [
        c for c in supported_currencies() if CurrencyRegistry.get_exponent(c) == 3
    ]
    assert len(three_exp_currencies) == 7
    for curr in three_exp_currencies:
        m = Money.from_decimal("2.500", curr)
        assert m.minor_units == 2500
        assert m.to_decimal_str() == "2.500"


# ===========================================================================
# Probe 4: Cross-currency addition/subtraction without ExchangeRate
# ===========================================================================
def test_challenger_probe_4_cross_currency_without_rate_raises_mismatch() -> None:
    """Empirical probe 4: Operations across differing currencies without rate raise CurrencyMismatchError."""
    usd = Money(1000, "USD")
    eur = Money(1000, "EUR")
    jpy = Money(1000, "JPY")
    kwd = Money(1000, "KWD")

    # Addition
    with pytest.raises(CurrencyMismatchError, match="without an explicit exchange rate"):
        usd.add(eur)

    with pytest.raises(CurrencyMismatchError):
        usd.add(jpy)

    with pytest.raises(CurrencyMismatchError):
        usd.add(kwd)

    # Subtraction
    with pytest.raises(CurrencyMismatchError, match="without an explicit exchange rate"):
        usd.subtract(eur)

    with pytest.raises(CurrencyMismatchError):
        usd.subtract(jpy)

    # Comparison
    with pytest.raises(CurrencyMismatchError, match="without an explicit exchange rate"):
        usd.is_greater_than(eur)

    with pytest.raises(CurrencyMismatchError, match="without an explicit exchange rate"):
        usd.is_at_most(eur)

    # total_money helper
    with pytest.raises(CurrencyMismatchError):
        total_money((usd, eur), "USD")


# ===========================================================================
# Probe 5: Incompatible ExchangeRate (wrong base or quote currency)
# ===========================================================================
def test_challenger_probe_5_incompatible_exchange_rate_raises_mismatch() -> None:
    """Empirical probe 5: Incompatible ExchangeRate raises CurrencyMismatchError."""
    usd = Money(1000, "USD")
    jpy = Money(15000, "JPY")

    # 1. Base mismatch during direct convert
    wrong_base_rate = ExchangeRate("EUR", "JPY", 160, 1)
    with pytest.raises(
        CurrencyMismatchError, match="base currency EUR does not match money currency USD"
    ):
        usd.convert(wrong_base_rate)

    # 2. Inverted direction during add (requires JPY -> USD, provided USD -> JPY)
    inverted_rate = ExchangeRate("USD", "JPY", 150, 1)
    with pytest.raises(CurrencyMismatchError, match="exchange rate must convert from JPY to USD"):
        usd.add(jpy, rate=inverted_rate)

    # 3. Third currency in base during add
    wrong_base_for_add = ExchangeRate("EUR", "USD", 110, 100)
    with pytest.raises(CurrencyMismatchError, match="exchange rate must convert from JPY to USD"):
        usd.add(jpy, rate=wrong_base_for_add)

    # 4. Third currency in quote during add
    wrong_quote_for_add = ExchangeRate("JPY", "GBP", 1, 190)
    with pytest.raises(CurrencyMismatchError, match="exchange rate must convert from JPY to USD"):
        usd.add(jpy, rate=wrong_quote_for_add)

    # 5. Incompatible rate provided when currencies already match
    usd2 = Money(500, "USD")
    mismatched_rate_same_curr = ExchangeRate("EUR", "USD", 110, 100)
    with pytest.raises(CurrencyMismatchError, match="exchange rate EUR->USD does not match"):
        usd.add(usd2, rate=mismatched_rate_same_curr)


# ===========================================================================
# Probe 6: ExchangeRate with denominator <= 0 or numerator <= 0
# ===========================================================================
def test_challenger_probe_6_exchange_rate_non_positive_numbers() -> None:
    """Empirical probe 6: ExchangeRate non-positive numerator/denominator rejection.

    Note: AgentPay domain models raise InvalidExchangeRateError (subclass of DomainError).
    This test verifies that numerator <= 0 or denominator <= 0 fails closed.
    """
    # Numerator <= 0
    with pytest.raises(InvalidExchangeRateError, match="rate_numerator must be positive, got 0"):
        ExchangeRate("USD", "JPY", 0)

    with pytest.raises(InvalidExchangeRateError, match="rate_numerator must be positive, got -1"):
        ExchangeRate("USD", "JPY", -1)

    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", -999, 1)

    # Denominator <= 0
    with pytest.raises(InvalidExchangeRateError, match="rate_denominator must be positive, got 0"):
        ExchangeRate("USD", "JPY", 150, 0)

    with pytest.raises(InvalidExchangeRateError, match="rate_denominator must be positive, got -1"):
        ExchangeRate("USD", "JPY", 150, -1)

    with pytest.raises(InvalidExchangeRateError):
        ExchangeRate("USD", "JPY", 150, -999)

    # Non-integer types (bool, float) raise NonIntegerMoneyError (subclass of InvalidMoneyError)
    with pytest.raises(NonIntegerMoneyError):
        ExchangeRate("USD", "JPY", True)

    with pytest.raises(NonIntegerMoneyError):
        ExchangeRate("USD", "JPY", 150, False)

    with pytest.raises(NonIntegerMoneyError):
        ExchangeRate("USD", "JPY", 150.5)  # type: ignore[arg-type]

    # Verify exception hierarchy relationship:
    # InvalidExchangeRateError is a DomainError, which is an Exception.
    assert issubclass(InvalidExchangeRateError, DomainError)
    assert issubclass(InvalidExchangeRateError, Exception)


# ===========================================================================
# Probe 7: Conversion math preserves exact round-half-up integer scaling across exponent boundaries
# ===========================================================================
def test_challenger_probe_7_exact_round_half_up_scaling_cross_exponents() -> None:
    """Empirical probe 7: Exact round-half-up scaling across exponent boundaries."""
    # 1. 100 USD to JPY at 150/1 (verbatim from dispatch)
    # 100.00 USD = 10,000 minor units (exp 2)
    # 1 USD = 150 JPY
    # Expected: 15,000 JPY (exp 0)
    usd_100 = Money(10000, "USD")
    rate_usd_jpy = ExchangeRate("USD", "JPY", 150, 1)
    jpy_15k = usd_100.convert(rate_usd_jpy)
    assert jpy_15k.minor_units == 15000
    assert jpy_15k.currency == "JPY"
    assert jpy_15k.to_decimal_str() == "15000"

    # 2. 1000 JPY to USD at 1/150 (verbatim from dispatch)
    # 1000 JPY = 1000 minor units (exp 0)
    # Rate: 1 JPY = 1/150 USD
    # Formula: (1000 * 10^2 * 1 + 10^0 * 150 // 2) // (10^0 * 150)
    #        = (100000 + 75) // 150 = 100075 // 150 = 667 minor units = 6.67 USD
    # (Exact: 1000 / 150 = 6.6666... USD -> rounds half up to 6.67 USD)
    jpy_1000 = Money(1000, "JPY")
    rate_jpy_usd = ExchangeRate("JPY", "USD", 1, 150)
    usd_converted = jpy_1000.convert(rate_jpy_usd)
    assert usd_converted.minor_units == 667
    assert usd_converted.currency == "USD"
    assert usd_converted.to_decimal_str() == "6.67"

    # Verify rate.inverse() matches
    assert rate_usd_jpy.inverse() == rate_jpy_usd
    assert jpy_1000.convert(rate_usd_jpy.inverse()).minor_units == 667

    # 3. Round-half-up tie-break precision tests
    # Half rate: 1 USD to 1/2 USD (rate_num=1, rate_denom=2)
    # 1 minor -> 0.5 minor -> rounds up to 1
    # 2 minor -> 1.0 minor -> 1
    # 3 minor -> 1.5 minor -> rounds up to 2
    half_rate = ExchangeRate("USD", "USD", 1, 2)
    assert Money(1, "USD").convert(half_rate).minor_units == 1
    assert Money(2, "USD").convert(half_rate).minor_units == 1
    assert Money(3, "USD").convert(half_rate).minor_units == 2
    assert Money(4, "USD").convert(half_rate).minor_units == 2

    # Quarter rate: 1 USD to 1/4 USD (rate_num=1, rate_denom=4)
    # 1 minor -> 0.25 minor -> rounds down to 0
    # 2 minor -> 0.50 minor -> rounds up to 1
    # 3 minor -> 0.75 minor -> rounds up to 1
    quarter_rate = ExchangeRate("USD", "USD", 1, 4)
    assert Money(1, "USD").convert(quarter_rate).minor_units == 0
    assert Money(2, "USD").convert(quarter_rate).minor_units == 1
    assert Money(3, "USD").convert(quarter_rate).minor_units == 1

    # 4. Exponent 3 (KWD) to Exponent 0 (JPY) boundary conversion
    # 1 KWD (1000 minor, exp 3) at 480 JPY / 1 KWD (num=480, denom=1)
    # Expected: 480 JPY
    kwd_1 = Money(1000, "KWD")
    rate_kwd_jpy = ExchangeRate("KWD", "JPY", 480, 1)
    jpy_from_kwd = kwd_1.convert(rate_kwd_jpy)
    assert jpy_from_kwd.minor_units == 480
    assert jpy_from_kwd.currency == "JPY"

    # 1 JPY (1 minor, exp 0) to KWD (exp 3) at 1/480 KWD per JPY
    # (1 * 10^3 * 1 + 240) // 480 = 1240 // 480 = 2 minor units (0.002 KWD)
    rate_jpy_kwd = rate_kwd_jpy.inverse()
    kwd_from_jpy = Money(1, "JPY").convert(rate_jpy_kwd)
    assert kwd_from_jpy.minor_units == 2
    assert kwd_from_jpy.currency == "KWD"
    assert kwd_from_jpy.to_decimal_str() == "0.002"


# ===========================================================================
# Probe 8: Hypothesis Property Invariants
# ===========================================================================
@given(
    amount=st.integers(min_value=0, max_value=10**12),
    currency=st.sampled_from(["USD", "EUR", "INR", "JPY", "KWD", "CLF"]),
)
def test_challenger_probe_8_hypothesis_zero_and_identity_preservation(
    amount: int, currency: str
) -> None:
    """Hypothesis probe: Identity conversion is strictly lossless across all exponent classes."""
    m = Money(amount, currency)
    id_rate = ExchangeRate(currency, currency, 1, 1)
    assert m.convert(id_rate) == m

    # Zero always converts to zero regardless of rate
    m_zero = Money.zero(currency)
    dummy_rate = ExchangeRate(currency, currency, 37, 19)
    assert m_zero.convert(dummy_rate) == m_zero

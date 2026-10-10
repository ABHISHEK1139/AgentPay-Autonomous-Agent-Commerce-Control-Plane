"""Empirical adversarial stress-testing harness for Money and CurrencyRegistry (TD-002, §122, §123).

Exhaustively probes:
1. Probe 1: Full ISO 4217 registry round-trip: Money.from_decimal(m.to_decimal_str(), m.currency) == m across ALL 58 currencies.
2. Probe 2: Zero-decimal currency invariants: m.minor_units == int(m.to_decimal_str()) and '.' not in m.to_decimal_str().
3. Probe 3: Extreme value boundaries: zero, small, large (up to 10^18), and boundary minor unit values.
4. Probe 4: Malformed, float, bool, negative, and over-precision input rejection (fail-closed security).
5. Probe 5: ExchangeRate exact rational math, round-half-up tie-breaking, and cross-currency arithmetic boundaries.
6. Probe 6: Multi-threaded concurrent stress test (50 threads performing simultaneous conversions and arithmetic).
"""

from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))

from agentpay.domain.currencies import (
    CurrencyRegistry,
    supported_currencies,
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
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ===========================================================================
# Probe 1: Full Registry Round-Trip across ALL 58 Currencies
# ===========================================================================
def probe_full_registry_roundtrip() -> dict[str, Any]:
    """Verify Money.from_decimal(m.to_decimal_str(), m.currency) == m for every supported currency."""
    all_currencies = supported_currencies()
    test_amounts = [
        0,
        1,
        2,
        5,
        9,
        10,
        11,
        99,
        100,
        101,
        999,
        1000,
        1001,
        9999,
        10000,
        149900,
        1000000,
        123456789,
        10**12,
        10**15,
        10**18,
    ]

    violations: list[str] = []
    total_checks = 0

    for currency in all_currencies:
        for amount in test_amounts:
            total_checks += 1
            m = Money(amount, currency)
            dec_str = m.to_decimal_str()
            try:
                recovered = Money.from_decimal(dec_str, currency)
            except Exception as e:
                violations.append(
                    f"Roundtrip failed to parse {dec_str!r} for {currency}: {type(e).__name__}: {e}"
                )
                continue

            if recovered != m:
                violations.append(
                    f"Roundtrip value mismatch for {currency}: expected {m}, got {recovered} (dec_str={dec_str!r})"
                )
            if recovered.minor_units != m.minor_units:
                violations.append(
                    f"Minor units mismatch for {currency}: expected {m.minor_units}, got {recovered.minor_units}"
                )
            if recovered.currency != m.currency:
                violations.append(
                    f"Currency mismatch for {currency}: expected {m.currency}, got {recovered.currency}"
                )

    return {
        "probe": "probe_full_registry_roundtrip",
        "passed": len(violations) == 0,
        "total_currencies": len(all_currencies),
        "total_checks": total_checks,
        "violations": violations,
    }


# ===========================================================================
# Probe 2: Zero-Decimal Currencies Invariants
# ===========================================================================
def probe_zero_decimal_invariants() -> dict[str, Any]:
    """Verify zero-decimal currencies satisfy m.minor_units == int(m.to_decimal_str()) and '.' not in string."""
    all_currencies = supported_currencies()
    zero_decimal_currencies = [c for c in all_currencies if CurrencyRegistry.get_exponent(c) == 0]

    test_amounts = [
        0,
        1,
        2,
        7,
        10,
        42,
        100,
        500,
        1500,
        999999,
        12345678,
        10**12,
        10**18,
    ]

    violations: list[str] = []
    total_checks = 0

    for currency in zero_decimal_currencies:
        for amount in test_amounts:
            total_checks += 1
            m = Money(amount, currency)
            dec_str = m.to_decimal_str()

            # Invariant 1: No decimal point
            if "." in dec_str:
                violations.append(
                    f"Zero-decimal currency {currency} decimal string contains dot: {dec_str!r}"
                )

            # Invariant 2: String parses as integer equal to minor_units
            try:
                parsed_int = int(dec_str)
            except ValueError as e:
                violations.append(
                    f"Zero-decimal currency {currency} dec_str {dec_str!r} failed int(): {e}"
                )
                continue

            if parsed_int != m.minor_units:
                violations.append(
                    f"Zero-decimal currency {currency} int({dec_str!r})={parsed_int} != minor_units={m.minor_units}"
                )

            # Invariant 3: format_amount matches exact f"{amount} {currency}"
            expected_formatted = f"{amount} {currency}"
            if m.format_amount() != expected_formatted:
                violations.append(
                    f"Zero-decimal currency {currency} format_amount={m.format_amount()!r} != {expected_formatted!r}"
                )

            # Invariant 4: from_major produces exact same minor_units
            from_maj = Money.from_major(amount, currency)
            if from_maj.minor_units != amount:
                violations.append(
                    f"Zero-decimal currency {currency} from_major({amount}) produced {from_maj.minor_units} != {amount}"
                )

    return {
        "probe": "probe_zero_decimal_invariants",
        "passed": len(violations) == 0,
        "zero_decimal_currencies_count": len(zero_decimal_currencies),
        "total_checks": total_checks,
        "violations": violations,
    }


# ===========================================================================
# Probe 3: Non-Zero Decimal Formatting & Decimal Places Structure
# ===========================================================================
def probe_decimal_structure_invariants() -> dict[str, Any]:
    """Verify that for exponent > 0, to_decimal_str has exact decimal places matching exponent."""
    all_currencies = supported_currencies()
    non_zero_currencies = [c for c in all_currencies if CurrencyRegistry.get_exponent(c) > 0]

    test_amounts = [0, 1, 9, 10, 99, 100, 101, 1000, 123456, 10**9]
    violations: list[str] = []
    total_checks = 0

    for currency in non_zero_currencies:
        exp = CurrencyRegistry.get_exponent(currency)
        for amount in test_amounts:
            total_checks += 1
            m = Money(amount, currency)
            dec_str = m.to_decimal_str()

            if "." not in dec_str:
                violations.append(
                    f"Non-zero currency {currency} (exp={exp}) dec_str has no dot: {dec_str!r}"
                )
                continue

            parts = dec_str.split(".")
            if len(parts) != 2:
                violations.append(
                    f"Non-zero currency {currency} dec_str has multiple dots: {dec_str!r}"
                )
                continue

            major_part, frac_part = parts
            if len(frac_part) != exp:
                violations.append(
                    f"Currency {currency} (exp={exp}) fractional part length {len(frac_part)} != {exp}: {dec_str!r}"
                )

            # Check that major * 10^exp + frac == minor_units
            recalc = int(major_part) * (10**exp) + int(frac_part)
            if recalc != amount:
                violations.append(
                    f"Currency {currency} recalculation {recalc} != {amount} from {dec_str!r}"
                )

    return {
        "probe": "probe_decimal_structure_invariants",
        "passed": len(violations) == 0,
        "non_zero_currencies_count": len(non_zero_currencies),
        "total_checks": total_checks,
        "violations": violations,
    }


# ===========================================================================
# Probe 4: Malformed, Float, Negative, and Over-Precision Rejection
# ===========================================================================
def probe_fail_closed_rejections() -> dict[str, Any]:
    """Verify strict fail-closed rejection on invalid, float, bool, and malformed inputs."""
    violations: list[str] = []
    checks = 0

    # 1. Negative amounts in Money constructor
    checks += 1
    try:
        Money(-1, "USD")
        violations.append("Money allowed negative minor_units")
    except NegativeMoneyError:
        pass
    except Exception as e:
        violations.append(f"Money(-1, 'USD') raised wrong error: {type(e).__name__}")

    # 2. Float amounts in Money constructor
    for val in [10.5, 0.0, 100.0, -0.5]:
        checks += 1
        try:
            Money(val, "USD")  # type: ignore[arg-type]
            violations.append(f"Money allowed float minor_units {val}")
        except NonIntegerMoneyError:
            pass
        except Exception as e:
            violations.append(f"Money({val}, 'USD') raised wrong error: {type(e).__name__}")

    # 3. Boolean values in Money constructor
    for val in [True, False]:
        checks += 1
        try:
            Money(val, "USD")  # type: ignore[arg-type]
            violations.append(f"Money allowed bool minor_units {val}")
        except NonIntegerMoneyError:
            pass
        except Exception as e:
            violations.append(f"Money({val}, 'USD') raised wrong error: {type(e).__name__}")

    # 4. Invalid currency in Money constructor
    for curr in ["", "US", "USDD", "123", "usd1", "ZZZ", "XYZ"]:
        checks += 1
        try:
            Money(100, curr)
            violations.append(f"Money allowed invalid currency {curr!r}")
        except InvalidCurrencyError:
            pass
        except Exception as e:
            violations.append(f"Money(100, {curr!r}) raised wrong error: {type(e).__name__}")

    # 5. Over-precision in from_decimal
    # USD has exp 2: 3 decimal places must fail
    checks += 1
    try:
        Money.from_decimal("10.001", "USD")
        violations.append("from_decimal allowed 3 decimal places for USD (exp=2)")
    except InvalidMoneyError:
        pass
    except Exception as e:
        violations.append(f"from_decimal('10.001', 'USD') raised wrong error: {type(e).__name__}")

    # KWD has exp 3: 4 decimal places must fail
    checks += 1
    try:
        Money.from_decimal("10.0001", "KWD")
        violations.append("from_decimal allowed 4 decimal places for KWD (exp=3)")
    except InvalidMoneyError:
        pass
    except Exception as e:
        violations.append(f"from_decimal('10.0001', 'KWD') raised wrong error: {type(e).__name__}")

    # JPY has exp 0: non-zero fraction must fail
    checks += 1
    try:
        Money.from_decimal("100.1", "JPY")
        violations.append("from_decimal allowed non-zero fraction for JPY (exp=0)")
    except InvalidMoneyError:
        pass
    except Exception as e:
        violations.append(f"from_decimal('100.1', 'JPY') raised wrong error: {type(e).__name__}")

    # 6. Malformed strings in from_decimal
    malformed_strings = [
        "",
        "   ",
        ".",
        "+10",
        "+10.00",
        "-10",
        "-10.00",
        "10.0.0",
        "10.00a",
        "10..00",
        "$10.00",
        "10,00",
    ]
    for s in malformed_strings:
        checks += 1
        try:
            Money.from_decimal(s, "USD")
            violations.append(f"from_decimal allowed malformed string {s!r}")
        except (InvalidMoneyError, NegativeMoneyError):
            pass
        except Exception as e:
            violations.append(f"from_decimal({s!r}, 'USD') raised wrong error: {type(e).__name__}")

    # 7. Non-string inputs in from_decimal
    for bad_input in [10, 10.5, True, False, None, ["10.00"], {"val": "10.00"}]:
        checks += 1
        try:
            Money.from_decimal(bad_input, "USD")  # type: ignore[arg-type]
            violations.append(f"from_decimal allowed non-string input {bad_input!r}")
        except InvalidMoneyError:
            pass
        except Exception as e:
            violations.append(
                f"from_decimal({bad_input!r}, 'USD') raised wrong error: {type(e).__name__}"
            )

    return {
        "probe": "probe_fail_closed_rejections",
        "passed": len(violations) == 0,
        "checks_count": checks,
        "violations": violations,
    }


# ===========================================================================
# Probe 5: ExchangeRate Precision & Cross-Currency Boundaries
# ===========================================================================
def probe_exchange_rate_and_arithmetic() -> dict[str, Any]:
    """Verify ExchangeRate rational math, round-half-up, and fail-closed cross-currency operations."""
    violations: list[str] = []
    checks = 0

    # 1. Identity conversion across all currencies
    all_currencies = supported_currencies()
    for curr in all_currencies:
        checks += 1
        id_rate = ExchangeRate(curr, curr, 1, 1)
        m = Money(12345, curr)
        converted = m.convert(id_rate)
        if converted != m:
            violations.append(
                f"Identity conversion failed for {curr}: expected {m}, got {converted}"
            )

    # 2. Exact round-half-up verification
    # Rate: 1 base = 1/2 quote.
    # 1 minor -> 0.5 minor -> rounds up to 1
    # 2 minor -> 1.0 minor -> 1
    # 3 minor -> 1.5 minor -> rounds up to 2
    # 4 minor -> 2.0 minor -> 2
    rate_half = ExchangeRate("USD", "USD", 1, 2)
    expected_round_half = [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (6, 3)]
    for val, exp_res in expected_round_half:
        checks += 1
        res = Money(val, "USD").convert(rate_half)
        if res.minor_units != exp_res:
            violations.append(
                f"Round-half-up tie break failed for val={val}: expected {exp_res}, got {res.minor_units}"
            )

    # 3. Cross-currency add/subtract/compare fails closed without rate
    checks += 1
    usd = Money(100, "USD")
    jpy = Money(100, "JPY")
    try:
        usd.add(jpy)
        violations.append("Cross-currency add without rate did not raise CurrencyMismatchError")
    except CurrencyMismatchError:
        pass

    checks += 1
    try:
        usd.subtract(jpy)
        violations.append(
            "Cross-currency subtract without rate did not raise CurrencyMismatchError"
        )
    except CurrencyMismatchError:
        pass

    checks += 1
    try:
        usd.is_greater_than(jpy)
        violations.append(
            "Cross-currency is_greater_than without rate did not raise CurrencyMismatchError"
        )
    except CurrencyMismatchError:
        pass

    checks += 1
    try:
        usd.is_at_most(jpy)
        violations.append(
            "Cross-currency is_at_most without rate did not raise CurrencyMismatchError"
        )
    except CurrencyMismatchError:
        pass

    # 4. Inverted rate direction fails closed
    wrong_dir_rate = ExchangeRate("USD", "JPY", 150, 1)
    checks += 1
    try:
        usd.add(jpy, rate=wrong_dir_rate)  # should require JPY -> USD
        violations.append("add allowed inverted rate direction")
    except CurrencyMismatchError:
        pass

    # 5. Invalid ExchangeRate definitions fail closed
    invalid_rates: list[tuple[int, int]] = [(0, 1), (-1, 1), (1, 0), (1, -1)]
    for num, den in invalid_rates:
        checks += 1
        try:
            ExchangeRate("USD", "EUR", num, den)
            violations.append(f"ExchangeRate allowed invalid rate ({num}, {den})")
        except InvalidExchangeRateError:
            pass

    return {
        "probe": "probe_exchange_rate_and_arithmetic",
        "passed": len(violations) == 0,
        "checks_count": checks,
        "violations": violations,
    }


# ===========================================================================
# Probe 6: Concurrent Multi-Threaded Stress Test
# ===========================================================================
def probe_concurrent_stress() -> dict[str, Any]:
    """Verify thread-safety and zero-race-condition behavior under high thread concurrency."""
    violations: list[str] = []
    rate = ExchangeRate("USD", "JPY", 150, 1)

    def worker_task(thread_id: int) -> list[str]:
        local_violations: list[str] = []
        for i in range(100):
            val = (thread_id * 100 + i) % 100000 + 1
            m_usd = Money(val, "USD")
            dec_str = m_usd.to_decimal_str()
            m_rec = Money.from_decimal(dec_str, "USD")
            if m_rec != m_usd:
                local_violations.append(f"Thread {thread_id} roundtrip failed for {val}")

            m_jpy = m_usd.convert(rate)
            # formula: (val * 10^0 * 150 + (10^2 * 1)//2) // (10^2 * 1) = (val * 150 + 50) // 100
            expected_jpy_round = (val * 150 + 50) // 100
            if m_jpy.minor_units != expected_jpy_round:
                local_violations.append(
                    f"Thread {thread_id} convert {val} USD -> JPY got {m_jpy.minor_units}, expected {expected_jpy_round}"
                )
        return local_violations

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(worker_task, tid) for tid in range(50)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            violations.extend(res)

    return {
        "probe": "probe_concurrent_stress",
        "passed": len(violations) == 0,
        "threads_executed": 50,
        "operations_per_thread": 100,
        "total_concurrent_ops": 5000,
        "violations": violations,
    }


# ===========================================================================
# Main Execution Runner
# ===========================================================================
def main() -> int:
    log("=================================================================")
    log("Running Empirical Stress Test Probes for Money & CurrencyRegistry")
    log("=================================================================")

    probes = [
        probe_full_registry_roundtrip,
        probe_zero_decimal_invariants,
        probe_decimal_structure_invariants,
        probe_fail_closed_rejections,
        probe_exchange_rate_and_arithmetic,
        probe_concurrent_stress,
    ]

    all_passed = True
    total_violations = 0

    for probe in probes:
        result = probe()
        probe_name = result["probe"]
        passed = result["passed"]
        violations = result["violations"]

        if passed:
            log(f"  [PASS] {probe_name}: {result}")
        else:
            log(f"  [FAIL] {probe_name}:")
            for v in violations:
                log(f"    - {v}")
            all_passed = False
            total_violations += len(violations)

    log("-----------------------------------------------------------------")
    if all_passed:
        log("ALL ADVERSARIAL PROBES PASSED CLEANLY (0 violations).")
        return 0
    else:
        log(f"ADVERSARIAL PROBES FAILED with {total_violations} total violations.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

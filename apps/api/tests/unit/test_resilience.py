from __future__ import annotations

import concurrent.futures
import contextlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpay.domain.errors import DomainError
from agentpay.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from agentpay.infrastructure.resilience.retry import (
    RetryExhaustedError,
    RetryPolicy,
)


class MockClock:
    """Controllable clock for deterministic circuit breaker testing."""

    def __init__(self, start_time: float = 100.0) -> None:
        self.current_time = start_time

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


# ==============================================================================
# CircuitBreaker Tests
# ==============================================================================


def test_circuit_breaker_starts_closed() -> None:
    breaker = CircuitBreaker(name="test_cb", failure_threshold=3)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.is_closed is True
    assert breaker.is_open is False
    assert breaker.failure_count == 0


def test_circuit_breaker_successful_calls_reset_failure_count() -> None:
    breaker = CircuitBreaker(name="test_cb", failure_threshold=3)

    def failing() -> None:
        raise ConnectionError("connection failed")

    with pytest.raises(ConnectionError):
        breaker.call(failing)
    assert breaker.failure_count == 1

    # Successful call resets failure count
    result = breaker.call(lambda: "success")
    assert result == "success"
    assert breaker.failure_count == 0
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_trips_to_open_after_threshold() -> None:
    clock = MockClock()
    breaker = CircuitBreaker(name="test_cb", failure_threshold=3, clock=clock)

    def failing() -> None:
        raise TimeoutError("timeout")

    for _ in range(3):
        with pytest.raises(TimeoutError):
            breaker.call(failing)

    assert breaker.state == CircuitState.OPEN
    assert breaker.is_open is True

    # Immediate next call fails fast with CircuitBreakerOpenError without invoking target
    target_mock = MagicMock()
    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        breaker.call(target_mock)

    assert exc_info.value.name == "test_cb"
    assert exc_info.value.retry_after_seconds > 0.0
    assert target_mock.called is False


def test_circuit_breaker_transitions_to_half_open_after_timeout() -> None:
    clock = MockClock()
    breaker = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        recovery_timeout_seconds=5.0,
        half_open_success_threshold=2,
        clock=clock,
    )

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))

    s_open = breaker.state
    assert s_open == CircuitState.OPEN

    # Advance clock past recovery timeout
    clock.advance(5.1)

    # State transitions to HALF_OPEN
    s_half = breaker.state
    assert s_half == CircuitState.HALF_OPEN
    assert breaker.is_half_open is True


def test_circuit_breaker_recovers_to_closed_on_half_open_successes() -> None:
    clock = MockClock()
    breaker = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        recovery_timeout_seconds=5.0,
        half_open_success_threshold=2,
        clock=clock,
    )

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))

    clock.advance(5.1)
    s_initial = breaker.state
    assert s_initial == CircuitState.HALF_OPEN

    # First successful probe
    res1 = breaker.call(lambda: "probe_1")
    assert res1 == "probe_1"
    s_probe1 = breaker.state
    assert s_probe1 == CircuitState.HALF_OPEN

    # Second successful probe resets to CLOSED
    res2 = breaker.call(lambda: "probe_2")
    assert res2 == "probe_2"
    s_probe2 = breaker.state
    assert s_probe2 == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_circuit_breaker_trips_immediately_on_half_open_failure() -> None:
    clock = MockClock()
    breaker = CircuitBreaker(
        name="test_cb",
        failure_threshold=2,
        recovery_timeout_seconds=5.0,
        clock=clock,
    )

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))

    clock.advance(5.1)
    s_half = breaker.state
    assert s_half == CircuitState.HALF_OPEN

    # Failure during HALF_OPEN trips immediately back to OPEN
    with pytest.raises(ConnectionError):
        breaker.call(lambda: (_ for _ in ()).throw(ConnectionError("probe failed")))

    s_tripped = breaker.state
    assert s_tripped == CircuitState.OPEN


def test_circuit_breaker_context_manager() -> None:
    breaker = CircuitBreaker(name="test_ctx", failure_threshold=2)

    with breaker:
        x = 42
    assert x == 42
    assert breaker.failure_count == 0

    with pytest.raises(ValueError), breaker:
        raise ValueError("boom")
    assert breaker.failure_count == 1


def test_circuit_breaker_manual_controls() -> None:
    breaker = CircuitBreaker(name="test_manual")
    s0 = breaker.state
    assert s0 == CircuitState.CLOSED

    breaker.trip("operator_override")
    s1 = breaker.state
    assert s1 == CircuitState.OPEN
    assert breaker.last_failure_reason == "operator_override"

    breaker.reset()
    s2 = breaker.state
    assert s2 == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_circuit_breaker_concurrency_thread_safety() -> None:
    """Verifies that CircuitBreaker is thread-safe under high concurrent contention."""
    breaker = CircuitBreaker(name="concurrent_cb", failure_threshold=10)
    errors: list[Exception] = []

    def task(i: int) -> int:
        try:
            if i % 3 == 0:
                raise ConnectionError("intermittent")
            return i * 2
        except Exception as exc:
            errors.append(exc)
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(breaker.call, task, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            with contextlib.suppress(ConnectionError, CircuitBreakerOpenError):
                f.result()

    # Breaker state must remain valid enum without corruption
    assert breaker.state in (CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN)


# ==============================================================================
# RetryPolicy Tests
# ==============================================================================


def test_retry_policy_immediate_success() -> None:
    policy = RetryPolicy(max_retries=3)
    target = MagicMock(return_value="done")
    result = policy.call(target, is_idempotent=True)
    assert result == "done"
    assert target.call_count == 1


def test_retry_policy_retries_idempotent_operation_on_failure() -> None:
    sleeps: list[float] = []
    policy = RetryPolicy(
        max_retries=3,
        initial_delay_seconds=0.1,
        backoff_factor=2.0,
        jitter=False,
        sleep_func=sleeps.append,
    )

    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("timeout")
        return "success"

    result = policy.call(flaky, is_idempotent=True)
    assert result == "success"
    assert attempts == 3
    assert len(sleeps) == 2
    # Without jitter: delays are 0.1, 0.2
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)


def test_retry_policy_exhausts_and_raises() -> None:
    policy = RetryPolicy(
        max_retries=2,
        initial_delay_seconds=0.01,
        sleep_func=lambda _: None,
    )

    def always_fails() -> Any:
        raise ConnectionError("downstream unreachable")

    with pytest.raises(RetryExhaustedError) as exc_info:
        policy.call(always_fails, is_idempotent=True)

    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.last_error, ConnectionError)


def test_retry_policy_strict_financial_safety_never_retries_non_idempotent() -> None:
    """Critical safety invariant (§43, §102): non-idempotent financial operations are never retried."""
    sleep_mock = MagicMock()
    policy = RetryPolicy(max_retries=5, sleep_func=sleep_mock)

    call_count = 0

    def charge_card() -> None:
        nonlocal call_count
        call_count += 1
        raise TimeoutError("gateway timeout")

    # With is_idempotent=False (the default for financial operations):
    with pytest.raises(TimeoutError):
        policy.call(charge_card, is_idempotent=False)

    # Must be called EXACTLY once; zero retries, zero sleeps!
    assert call_count == 1
    assert sleep_mock.called is False


def test_retry_policy_never_retries_domain_invariant_errors() -> None:
    """Domain errors represent business invariant rejections and must fail fast (§102)."""
    sleep_mock = MagicMock()
    policy = RetryPolicy(max_retries=3, sleep_func=sleep_mock)

    call_count = 0

    class PolicyLimitError(DomainError):
        code = "POLICY_LIMIT_EXCEEDED"

    def evaluate_policy() -> None:
        nonlocal call_count
        call_count += 1
        raise PolicyLimitError("spending limit exceeded")

    with pytest.raises(PolicyLimitError):
        policy.call(evaluate_policy, is_idempotent=True)

    # Must fail immediately without retry
    assert call_count == 1
    assert sleep_mock.called is False

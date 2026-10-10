"""Thread-safe state-machine Circuit Breaker primitive (§103).

Transitions across CLOSED -> OPEN -> HALF_OPEN -> CLOSED to prevent cascading failures
and protect downstream services from overload during failure episodes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

from agentpay.domain.errors import DomainError

T = TypeVar("T")


class CircuitState(StrEnum):
    """Lifecycle state machine for a circuit breaker (§103)."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(DomainError):
    """Raised when an operation is rejected because the circuit breaker is OPEN (§103)."""

    code = "CIRCUIT_BREAKER_OPEN"

    def __init__(self, name: str, retry_after_seconds: float = 0.0) -> None:
        super().__init__(
            f"Circuit breaker '{name}' is OPEN. Fast fail-safe active. Retry after {retry_after_seconds:.2f}s"
        )
        self.name = name
        self.retry_after_seconds = retry_after_seconds


class CircuitBreaker:
    """Thread-safe, state-machine Circuit Breaker (§103).

    States:
    - CLOSED: Normal operation. Successes reset failure count. Failures increment failure count.
              If consecutive failures reach threshold, transitions to OPEN.
    - OPEN: Fast fail-safe. Invocations raise CircuitBreakerOpenError immediately.
            After recovery_timeout_seconds has elapsed, next invocation transitions to HALF_OPEN.
    - HALF_OPEN: Probing downstream health. Allows trial requests.
                 If half_open_success_threshold trials succeed, resets to CLOSED.
                 If any trial fails, immediately trips back to OPEN.
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 10.0,
        half_open_success_threshold: int = 2,
        expected_exceptions: tuple[type[Exception], ...] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout_seconds <= 0.0:
            raise ValueError("recovery_timeout_seconds must be positive")
        if half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be at least 1")

        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._half_open_success_threshold = half_open_success_threshold
        self._expected_exceptions = expected_exceptions or (Exception,)
        self._clock = clock

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._trial_success_count = 0
        self._last_state_change = self._clock()
        self._last_failure_reason: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_and_update_state_under_lock()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def failure_threshold(self) -> int:
        return self._failure_threshold

    @property
    def recovery_timeout_seconds(self) -> float:
        return self._recovery_timeout_seconds

    @property
    def last_failure_reason(self) -> str | None:
        with self._lock:
            return self._last_failure_reason

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

    def _check_and_update_state_under_lock(self) -> None:
        """Transitions from OPEN to HALF_OPEN if the recovery timeout has elapsed."""
        if self._state == CircuitState.OPEN:
            elapsed = self._clock() - self._last_state_change
            if elapsed >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._trial_success_count = 0
                self._last_state_change = self._clock()

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Executes func guarded by the circuit breaker state machine."""
        with self._lock:
            self._check_and_update_state_under_lock()

            if self._state == CircuitState.OPEN:
                elapsed = self._clock() - self._last_state_change
                retry_after = max(0.0, self._recovery_timeout_seconds - elapsed)
                raise CircuitBreakerOpenError(name=self._name, retry_after_seconds=retry_after)

        # Execute downstream invocation outside lock to avoid contention
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._record_failure(exc)
            raise

        self._record_success()
        return result

    def _record_success(self) -> None:
        """Records a successful call and transitions state if appropriate."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._trial_success_count += 1
                if self._trial_success_count >= self._half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._trial_success_count = 0
                    self._last_state_change = self._clock()
                    self._last_failure_reason = None
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
                self._last_failure_reason = None

    def _record_failure(self, exc: Exception) -> None:
        """Records a failed call and transitions state if threshold breached."""
        if not isinstance(exc, self._expected_exceptions):
            return

        with self._lock:
            self._last_failure_reason = str(exc) or type(exc).__name__

            if self._state == CircuitState.HALF_OPEN:
                # Any failure during HALF_OPEN immediately trips back to OPEN
                self._state = CircuitState.OPEN
                self._last_state_change = self._clock()
                self._trial_success_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = self._clock()

    def trip(self, reason: str = "manual_trip") -> None:
        """Manually trips the circuit breaker to OPEN state."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._last_state_change = self._clock()
            self._last_failure_reason = reason

    def reset(self) -> None:
        """Resets the circuit breaker to normal CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._trial_success_count = 0
            self._last_state_change = self._clock()
            self._last_failure_reason = None

    def __enter__(self) -> CircuitBreaker:
        with self._lock:
            self._check_and_update_state_under_lock()
            if self._state == CircuitState.OPEN:
                elapsed = self._clock() - self._last_state_change
                retry_after = max(0.0, self._recovery_timeout_seconds - elapsed)
                raise CircuitBreakerOpenError(name=self._name, retry_after_seconds=retry_after)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if exc_val is not None:
            if isinstance(exc_val, Exception) and not isinstance(exc_val, CircuitBreakerOpenError):
                self._record_failure(exc_val)
        else:
            self._record_success()

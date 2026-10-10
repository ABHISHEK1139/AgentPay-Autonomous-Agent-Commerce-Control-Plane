"""Bounded Exponential Backoff with Jitter Retry Policy (§102).

Enforces retry limits, exponential backoff with full jitter, and strict non-retryability
guards for financial mutations and non-idempotent operations (§43, §102).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from agentpay.domain.errors import DomainError

T = TypeVar("T")


class RetryExhaustedError(DomainError):
    """Raised when an operation has exhausted all configured retry attempts (§102)."""

    code = "RETRY_EXHAUSTED"

    def __init__(self, message: str, attempts: int, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class NonRetryableOperationError(DomainError):
    """Raised when a retry is attempted on a non-idempotent or financial mutation (§43, §102)."""

    code = "NON_RETRYABLE_OPERATION"


class RetryPolicy:
    """Bounded exponential backoff retry policy with full jitter (§102).

    Key Invariants:
    1. Financial operations (charges, captures, refunds, orders) must NEVER be blindly
       retried on error or timeout without idempotency guarantees (§43).
    2. Retries are strictly restricted to operations flagged as `is_idempotent=True`
       or read-only queries.
    3. Full jitter is applied by default to prevent thundering herd spikes.
    4. Domain invariant violations (DomainError) are deterministic and never retried.
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay_seconds: float = 0.1,
        max_delay_seconds: float = 5.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: tuple[type[Exception], ...] | None = None,
        non_retryable_exceptions: tuple[type[Exception], ...] | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
        random_func: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if max_retries > 10:
            raise ValueError("max_retries cannot exceed hard safety ceiling of 10")
        if initial_delay_seconds < 0.0:
            raise ValueError("initial_delay_seconds cannot be negative")
        if max_delay_seconds < initial_delay_seconds:
            raise ValueError("max_delay_seconds cannot be less than initial_delay_seconds")
        if backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")

        self._max_retries = max_retries
        self._initial_delay_seconds = initial_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._backoff_factor = backoff_factor
        self._jitter = jitter
        self._retryable_exceptions = retryable_exceptions or (
            TimeoutError,
            ConnectionError,
            OSError,
        )
        self._non_retryable_exceptions = non_retryable_exceptions or (DomainError,)
        self._sleep_func = sleep_func
        self._random_func = random_func

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def initial_delay_seconds(self) -> float:
        return self._initial_delay_seconds

    @property
    def max_delay_seconds(self) -> float:
        return self._max_delay_seconds

    @property
    def backoff_factor(self) -> float:
        return self._backoff_factor

    @property
    def jitter(self) -> bool:
        return self._jitter

    def calculate_delay(self, attempt: int) -> float:
        """Calculates backoff delay with exponential scaling and optional full jitter."""
        base_delay = min(
            self._max_delay_seconds,
            self._initial_delay_seconds * (self._backoff_factor**attempt),
        )
        if self._jitter and base_delay > 0.0:
            return self._random_func(0.0, base_delay)
        return base_delay

    def call(
        self,
        func: Callable[..., T],
        *args: Any,
        is_idempotent: bool = False,
        **kwargs: Any,
    ) -> T:
        """Executes func with retry gating.

        If `is_idempotent=False` (e.g. non-idempotent financial operations),
        executes func exactly once and immediately re-raises any exception without retrying.
        """
        # Non-idempotent guard: NEVER retry non-idempotent operations (§43, §102)
        if not is_idempotent:
            return func(*args, **kwargs)

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_error = exc

                # Deterministic domain errors and non-retryable types are never retried
                if isinstance(exc, self._non_retryable_exceptions):
                    raise

                # Check if exception is in the retryable allowlist
                if not isinstance(exc, self._retryable_exceptions):
                    raise

                # If this was the last attempt, do not sleep; exhaust retries
                if attempt >= self._max_retries:
                    break

                delay = self.calculate_delay(attempt)
                self._sleep_func(delay)

        assert last_error is not None
        raise RetryExhaustedError(
            f"Operation failed after {self._max_retries + 1} attempts. Last error: {last_error}",
            attempts=self._max_retries + 1,
            last_error=last_error,
        ) from last_error

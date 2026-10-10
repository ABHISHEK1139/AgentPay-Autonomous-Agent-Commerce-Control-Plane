"""Thread-safe, in-memory token bucket rate limiter adapter (§52, §62).

Implements the Token Bucket algorithm for rate limiting and request throttling
across tenant, agent, and tool boundaries. Uses threading.Lock() to guarantee
thread safety under concurrent request bursts without race conditions or
double-consumption of tokens.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from agentpay.application.ports.rate_limit_port import RateLimiter, RateLimitResult


@dataclass(slots=True)
class _TokenBucketState:
    """Internal state of a single token bucket."""

    tokens: float
    last_refill: float
    capacity: int
    refill_rate_per_sec: float


class InMemoryRateLimiter(RateLimiter):
    """Thread-safe in-memory token bucket rate limiter.

    Guarantees:
    - Atomicity: Token check and consumption occur under a reentrant or standard lock.
    - Determinism: Refill calculation based on elapsed time:
        tokens = min(capacity, current_tokens + elapsed * refill_rate).
    - Accurate Retry-After: When tokens < cost, computes exact seconds until enough
        tokens refill: (cost - current_tokens) / refill_rate.
    """

    def __init__(self, time_func: Callable[[], float] | None = None) -> None:
        """Initialize the rate limiter.

        Args:
            time_func: Optional callable returning current timestamp in seconds.
                Defaults to time.time. Useful for injecting deterministic clocks in tests.
        """
        self._lock = threading.Lock()
        self._buckets: dict[str, _TokenBucketState] = {}
        self._time_func: Callable[[], float] = time_func if time_func is not None else time.time

    def check_rate_limit(
        self,
        key: str,
        cost: int = 1,
        capacity: int = 10,
        refill_rate_per_sec: float = 2.0,
    ) -> RateLimitResult:
        """Check rate limit for a key and consume tokens if allowed.

        Args:
            key: Rate limit key (e.g., 'agent:tenant_01:usr_01' or 'tenant:tenant_01').
            cost: Cost in tokens (default: 1).
            capacity: Maximum burst capacity of the token bucket (default: 10).
            refill_rate_per_sec: Tokens refilled per second (default: 2.0).

        Returns:
            RateLimitResult containing allowed flag, retry_after_seconds, remaining tokens,
            bucket limit, and reset_time.
        """
        if cost < 0:
            raise ValueError(f"Cost must be non-negative, got {cost}")
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")
        if refill_rate_per_sec <= 0:
            raise ValueError(f"Refill rate must be positive, got {refill_rate_per_sec}")

        with self._lock:
            now = self._time_func()
            bucket = self._buckets.get(key)

            if bucket is None:
                # First request for this key: initialize with full capacity
                bucket = _TokenBucketState(
                    tokens=float(capacity),
                    last_refill=now,
                    capacity=capacity,
                    refill_rate_per_sec=refill_rate_per_sec,
                )
                self._buckets[key] = bucket
            else:
                # Existing bucket: refill based on elapsed time
                elapsed = max(0.0, now - bucket.last_refill)
                bucket.tokens = min(float(capacity), bucket.tokens + elapsed * refill_rate_per_sec)
                bucket.last_refill = now
                bucket.capacity = capacity
                bucket.refill_rate_per_sec = refill_rate_per_sec

            if bucket.tokens >= cost:
                bucket.tokens -= float(cost)
                remaining = int(bucket.tokens)
                time_to_full = (
                    (capacity - bucket.tokens) / refill_rate_per_sec
                    if refill_rate_per_sec > 0
                    else 0.0
                )
                return RateLimitResult(
                    allowed=True,
                    retry_after_seconds=0.0,
                    remaining=remaining,
                    limit=capacity,
                    reset_time=now + time_to_full,
                )
            else:
                needed = float(cost) - bucket.tokens
                retry_after = needed / refill_rate_per_sec if refill_rate_per_sec > 0 else 60.0
                remaining = max(0, int(bucket.tokens))
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=retry_after,
                    remaining=remaining,
                    limit=capacity,
                    reset_time=now + retry_after,
                )

    def reset(self, key: str) -> None:
        """Reset or clear the rate limit state for a given key."""
        with self._lock:
            self._buckets.pop(key, None)

    def reset_all(self) -> None:
        """Clear all rate limit buckets."""
        with self._lock:
            self._buckets.clear()

    def get_tokens(self, key: str) -> float | None:
        """Retrieve current tokens for a key after applying refill (for inspection/testing)."""
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return None
            now = self._time_func()
            elapsed = max(0.0, now - bucket.last_refill)
            return min(float(bucket.capacity), bucket.tokens + elapsed * bucket.refill_rate_per_sec)

    def get_bucket_count(self) -> int:
        """Return the number of tracked buckets."""
        with self._lock:
            return len(self._buckets)

"""Unit tests for InMemoryRateLimiter and token bucket math (§52, §62)."""

from __future__ import annotations

import concurrent.futures

import pytest

from agentpay.adapters.in_memory.rate_limiter import InMemoryRateLimiter
from agentpay.application.ports.rate_limit_port import RateLimiter, RateLimitResult


def test_in_memory_rate_limiter_implements_protocol() -> None:
    limiter = InMemoryRateLimiter()
    assert isinstance(limiter, RateLimiter)


def test_token_bucket_initial_burst_and_consumption() -> None:
    """A fresh bucket permits up to its capacity and decrements remaining tokens."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Capacity = 5, cost = 1
    for i in range(5):
        res = limiter.check_rate_limit("user_01", cost=1, capacity=5, refill_rate_per_sec=1.0)
        assert res.allowed is True
        assert res.remaining == 4 - i
        assert res.limit == 5
        assert res.retry_after_seconds == 0.0

    # 6th attempt should be rejected
    res = limiter.check_rate_limit("user_01", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert res.allowed is False
    assert res.remaining == 0
    assert res.limit == 5
    assert res.retry_after_seconds == 1.0  # Needs 1 token, refill rate is 1 token/sec -> 1.0s


def test_token_bucket_exact_retry_after_calculation() -> None:
    """When tokens are insufficient, retry_after_seconds is mathematically exact."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Exhaust a bucket of capacity 2 with refill rate 0.5 tokens/sec (1 token every 2 seconds)
    limiter.check_rate_limit("key_01", cost=2, capacity=2, refill_rate_per_sec=0.5)

    # Next cost=1 request needs 1.0 token at 0.5 tokens/sec -> 2.0s
    res = limiter.check_rate_limit("key_01", cost=1, capacity=2, refill_rate_per_sec=0.5)
    assert res.allowed is False
    assert res.retry_after_seconds == 2.0

    # Next cost=2 request needs 2.0 tokens at 0.5 tokens/sec -> 4.0s
    res2 = limiter.check_rate_limit("key_01", cost=2, capacity=2, refill_rate_per_sec=0.5)
    assert res2.allowed is False
    assert res2.retry_after_seconds == 4.0


def test_token_bucket_refill_over_time() -> None:
    """Advancing the clock refills tokens at the configured refill rate."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Capacity 10, refill 2 tokens/sec. Exhaust all 10 tokens.
    res = limiter.check_rate_limit("key_01", cost=10, capacity=10, refill_rate_per_sec=2.0)
    assert res.allowed is True
    assert res.remaining == 0

    # Immediately blocked
    res_blocked = limiter.check_rate_limit("key_01", cost=1, capacity=10, refill_rate_per_sec=2.0)
    assert res_blocked.allowed is False

    # Advance clock by 1.5 seconds -> 1.5 * 2.0 = 3 tokens refilled
    current_time += 1.5

    # Should permit up to 3 tokens
    res_1 = limiter.check_rate_limit("key_01", cost=1, capacity=10, refill_rate_per_sec=2.0)
    assert res_1.allowed is True
    assert res_1.remaining == 2

    res_2 = limiter.check_rate_limit("key_01", cost=2, capacity=10, refill_rate_per_sec=2.0)
    assert res_2.allowed is True
    assert res_2.remaining == 0

    # 4th token blocked
    res_3 = limiter.check_rate_limit("key_01", cost=1, capacity=10, refill_rate_per_sec=2.0)
    assert res_3.allowed is False


def test_token_bucket_capacity_bound() -> None:
    """Tokens do not accumulate beyond the configured capacity even after long idle time."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Initialize bucket with capacity 5
    limiter.check_rate_limit("key_01", cost=1, capacity=5, refill_rate_per_sec=1.0)

    # Advance time by 10,000 seconds
    current_time += 10000.0

    # Tokens should cap at capacity (5), not 10,000
    tokens = limiter.get_tokens("key_01")
    assert tokens == 5.0

    # Consuming 5 tokens should succeed
    res = limiter.check_rate_limit("key_01", cost=5, capacity=5, refill_rate_per_sec=1.0)
    assert res.allowed is True
    assert res.remaining == 0

    # Consuming 1 more should fail
    res_blocked = limiter.check_rate_limit("key_01", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert res_blocked.allowed is False


def test_token_bucket_key_isolation() -> None:
    """Different keys operate in completely independent rate limit buckets."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Exhaust key_a
    res_a = limiter.check_rate_limit("key_a", cost=5, capacity=5, refill_rate_per_sec=1.0)
    assert res_a.allowed is True

    res_a_blocked = limiter.check_rate_limit("key_a", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert res_a_blocked.allowed is False

    # key_b is untouched and has full capacity
    res_b = limiter.check_rate_limit("key_b", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert res_b.allowed is True
    assert res_b.remaining == 4


def test_token_bucket_reset() -> None:
    """Calling reset(key) clears the state, restoring full burst capacity on next check."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    # Exhaust key
    limiter.check_rate_limit("key_01", cost=5, capacity=5, refill_rate_per_sec=1.0)
    assert (
        limiter.check_rate_limit("key_01", cost=1, capacity=5, refill_rate_per_sec=1.0).allowed
        is False
    )

    # Reset
    limiter.reset("key_01")
    assert limiter.get_tokens("key_01") is None

    # Next check gets a fresh bucket with full capacity
    res = limiter.check_rate_limit("key_01", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert res.allowed is True
    assert res.remaining == 4


def test_token_bucket_reset_all() -> None:
    """Calling reset_all() clears all tracked keys."""
    limiter = InMemoryRateLimiter()
    limiter.check_rate_limit("k1", cost=1, capacity=5, refill_rate_per_sec=1.0)
    limiter.check_rate_limit("k2", cost=1, capacity=5, refill_rate_per_sec=1.0)
    assert limiter.get_bucket_count() == 2

    limiter.reset_all()
    assert limiter.get_bucket_count() == 0


def test_token_bucket_zero_cost_probe() -> None:
    """A cost=0 check probes the bucket without consuming tokens."""
    limiter = InMemoryRateLimiter()
    res = limiter.check_rate_limit("k1", cost=0, capacity=10, refill_rate_per_sec=1.0)
    assert res.allowed is True
    assert res.remaining == 10

    # Ensure no token was deducted
    tokens = limiter.get_tokens("k1")
    assert tokens == 10.0


def test_token_bucket_invalid_arguments_raise() -> None:
    limiter = InMemoryRateLimiter()
    with pytest.raises(ValueError, match="Cost must be non-negative"):
        limiter.check_rate_limit("k1", cost=-1, capacity=10, refill_rate_per_sec=1.0)

    with pytest.raises(ValueError, match="Capacity must be positive"):
        limiter.check_rate_limit("k1", cost=1, capacity=0, refill_rate_per_sec=1.0)

    with pytest.raises(ValueError, match="Refill rate must be positive"):
        limiter.check_rate_limit("k1", cost=1, capacity=10, refill_rate_per_sec=0)


def test_token_bucket_thread_safety_concurrency() -> None:
    """50 concurrent threads requesting tokens from a capacity=20 bucket: exactly 20 succeed."""
    current_time = 1000.0
    # Freeze time so no refill occurs during the burst
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    num_threads = 50
    capacity = 20

    def attempt_request(idx: int) -> RateLimitResult:
        return limiter.check_rate_limit(
            key="concurrent_test", cost=1, capacity=capacity, refill_rate_per_sec=1.0
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(attempt_request, range(num_threads)))

    allowed_count = sum(1 for r in results if r.allowed)
    blocked_count = sum(1 for r in results if not r.allowed)

    assert allowed_count == capacity
    assert blocked_count == num_threads - capacity

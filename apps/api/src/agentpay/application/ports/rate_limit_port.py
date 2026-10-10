"""Application ports for rate limiting and request throttling (§52, §62).

Following Clean Architecture (§8, §10), this module defines the abstract
protocol and data transfer structures for rate limiting. Rate limiters
govern request flow across tenant, agent, and tool boundaries to prevent
denial-of-service, financial replay loops, and brute-force bursts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Result of a rate limit evaluation (§62).

    Attributes:
        allowed: True if the request is within budget and permitted to execute.
        retry_after_seconds: Exact seconds the caller must wait before retrying (0.0 if allowed).
        remaining: Number of remaining tokens/requests in the bucket after this check.
        limit: Total burst capacity or maximum limit configured for the bucket.
        reset_time: Monotonic or epoch timestamp when the bucket will completely refill or permit execution.
    """

    allowed: bool
    retry_after_seconds: float
    remaining: int
    limit: int
    reset_time: float


@runtime_checkable
class RateLimiter(Protocol):
    """Port for rate limiting and request throttling implementations."""

    def check_rate_limit(
        self,
        key: str,
        cost: int = 1,
        capacity: int = 10,
        refill_rate_per_sec: float = 2.0,
    ) -> RateLimitResult:
        """Check rate limit for a key and consume tokens if allowed.

        Args:
            key: Partitioning key (e.g. 'agent:tenant_01:agent_01' or 'tenant:tenant_01').
            cost: Number of tokens required for this operation (default 1).
            capacity: Maximum burst capacity of the token bucket.
            refill_rate_per_sec: Tokens added to the bucket per second.

        Returns:
            RateLimitResult indicating permission status, remaining tokens,
            and retry guidance.
        """
        ...

    def reset(self, key: str) -> None:
        """Reset or clear the rate limit state for a given key."""
        ...

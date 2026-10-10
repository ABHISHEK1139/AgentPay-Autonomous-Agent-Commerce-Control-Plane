"""Domain models for agent loop protection and financial safety budgets (§64, §65)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from agentpay.domain.errors import (
    FinancialSafetyBudgetExceededError,
    LoopBudgetExceededError,
    RateLimitExceededError,
)
from agentpay.domain.money import Money


@dataclass(frozen=True, slots=True)
class AgentLoopBudget:
    """Policy-defined limits preventing autonomous agent looping anomalies (§64)."""

    max_action_depth: int = 5
    max_tool_calls: int = 20
    max_wall_clock_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_action_depth <= 0:
            raise ValueError("max_action_depth must be positive")
        if self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive")
        if self.max_wall_clock_seconds <= 0.0:
            raise ValueError("max_wall_clock_seconds must be positive")


class AgentLoopTracker:
    """Tracks agent execution depth and tool calls to halt runaway loops (§64)."""

    def __init__(self, budget: AgentLoopBudget | None = None) -> None:
        self.budget = budget or AgentLoopBudget()
        self._current_depth: int = 0
        self._tool_call_count: int = 0

    @property
    def current_depth(self) -> int:
        return self._current_depth

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    def increment_depth(self) -> None:
        """Increment execution depth and halt if budget exceeded."""
        if self._current_depth >= self.budget.max_action_depth:
            raise LoopBudgetExceededError(
                f"Action depth limit {self.budget.max_action_depth} exceeded"
            )
        self._current_depth += 1

    def decrement_depth(self) -> None:
        """Decrement execution depth upon completing a nested action step."""
        if self._current_depth > 0:
            self._current_depth -= 1

    def record_tool_call(self) -> None:
        """Record an agent tool call and halt if budget exceeded."""
        if self._tool_call_count >= self.budget.max_tool_calls:
            raise LoopBudgetExceededError(f"Tool call limit {self.budget.max_tool_calls} exceeded")
        self._tool_call_count += 1


@dataclass(frozen=True, slots=True)
class FinancialSafetyBudget:
    """Financial exposure cap per execution context preventing compounding losses (§65)."""

    max_spend: Money
    max_transactions: int = 5
    max_refund_value: Money | None = None

    def __post_init__(self) -> None:
        if self.max_transactions <= 0:
            raise ValueError("max_transactions must be positive")
        if self.max_spend.minor_units < 0:
            raise ValueError("max_spend cannot be negative")


class FinancialSafetyTracker:
    """Monitors cumulative financial exposure during an agent session (§65)."""

    def __init__(self, budget: FinancialSafetyBudget) -> None:
        self.budget = budget
        self._cumulative_spend = Money(0, budget.max_spend.currency)
        self._transaction_count: int = 0

    @property
    def cumulative_spend(self) -> Money:
        return self._cumulative_spend

    @property
    def transaction_count(self) -> int:
        return self._transaction_count

    def record_transaction(self, amount: Money) -> None:
        """Record a transaction and ensure it does not breach the execution budget."""
        if self._transaction_count >= self.budget.max_transactions:
            raise FinancialSafetyBudgetExceededError(
                f"Maximum transaction count {self.budget.max_transactions} reached"
            )

        new_total = self._cumulative_spend.add(amount)
        if new_total.minor_units > self.budget.max_spend.minor_units:
            raise FinancialSafetyBudgetExceededError(
                f"Cumulative spend {new_total} exceeds safety budget {self.budget.max_spend}"
            )

        self._cumulative_spend = new_total
        self._transaction_count += 1


# ===========================================================================
# Rate Limiting Engine (§52, §62)
# ===========================================================================
@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Configuration for rate limiting an identity, tenant, or action scope (§62)."""

    requests_per_window: int = 60
    window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.requests_per_window <= 0:
            raise ValueError("requests_per_window must be positive")
        if self.window_seconds <= 0.0:
            raise ValueError("window_seconds must be positive")


class InMemoryRateLimiter:
    """Sliding-window rate limiter per tenant / agent / caller scope (§52, §62).

    Thread-safe implementation that tracks timestamps of requests within a sliding window.
    """

    def __init__(self, default_config: RateLimitConfig | None = None) -> None:
        self._lock = threading.Lock()
        self._default_config = default_config or RateLimitConfig()
        self._custom_configs: dict[str, RateLimitConfig] = {}
        self._history: dict[str, list[float]] = defaultdict(list)

    def set_config(self, key: str, config: RateLimitConfig) -> None:
        """Assign custom rate limit configuration for a specific tenant or agent scope."""
        with self._lock:
            self._custom_configs[key] = config

    def check(self, key: str, at: float | None = None) -> None:
        """Check if request is permitted under rate limit.

        Raises:
            RateLimitExceededError: If the maximum request threshold is breached.
        """
        now = at if at is not None else time.monotonic()
        with self._lock:
            config = self._custom_configs.get(key, self._default_config)
            cutoff = now - config.window_seconds
            valid_history = [t for t in self._history[key] if t > cutoff]
            if len(valid_history) >= config.requests_per_window:
                retry_after = round(max(0.1, valid_history[0] + config.window_seconds - now), 2)
                raise RateLimitExceededError(
                    f"Rate limit of {config.requests_per_window} requests per {config.window_seconds}s "
                    f"exceeded for '{key}'. Retry after {retry_after}s"
                )
            valid_history.append(now)
            self._history[key] = valid_history

    def reset(self, key: str | None = None) -> None:
        """Reset rate limiter history for a specific key, or globally if key is None."""
        with self._lock:
            if key is None:
                self._history.clear()
            else:
                self._history.pop(key, None)

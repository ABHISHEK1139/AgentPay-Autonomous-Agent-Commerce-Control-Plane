"""Domain models for agent loop protection and financial safety budgets (§64, §65)."""

from __future__ import annotations

from dataclasses import dataclass

from agentpay.domain.errors import (
    FinancialSafetyBudgetExceededError,
    LoopBudgetExceededError,
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

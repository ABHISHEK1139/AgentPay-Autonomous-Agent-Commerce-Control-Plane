"""Application ports for Approval workflows and Decision Intelligence (§20, §28).

Following Clean Architecture (§8, §10), every repository method requires
an explicit `tenant_id` partition parameter (§49, §50).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentpay.domain.approval import Approval
from agentpay.domain.decision import Decision, DecisionContext


@runtime_checkable
class ApprovalRepository(Protocol):
    """Port for human approval request persistence and lookup."""

    def get(self, tenant_id: str, approval_id: str) -> Approval | None:
        """Retrieve an approval record by ID within a tenant partition."""
        ...

    def save(self, tenant_id: str, approval: Approval) -> None:
        """Persist or update an approval record within a tenant partition."""
        ...

    def list_for_action(self, tenant_id: str, action_id: str) -> list[Approval]:
        """List all approvals associated with an action within a tenant partition."""
        ...

    def atomic_consume(self, tenant_id: str, approval_id: str) -> Approval:
        """Atomically verify and transition approval to CONSUMED under lock/transaction (§20)."""
        ...


@runtime_checkable
class DecisionEngine(Protocol):
    """Port for advisory decision intelligence / JEV risk evaluation (§28)."""

    def evaluate(self, context: DecisionContext) -> Decision:
        """Evaluate action context and produce advisory risk score and recommendations."""
        ...

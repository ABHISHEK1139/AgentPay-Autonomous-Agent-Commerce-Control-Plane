"""Thread-safe, tenant-partitioned in-memory approval repository (§20, §49, §50).

Enforces:
- Explicit multi-tenant data partitioning.
- Strict isolation preventing cross-tenant access.
"""

from __future__ import annotations

import threading

from agentpay.application.ports.approval_port import ApprovalRepository
from agentpay.domain.approval import (
    Approval,
    ApprovalAlreadyConsumedError,
    ApprovalStatus,
)
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidCommerceError,
)


class InMemoryApprovalRepository(ApprovalRepository):
    """Thread-safe in-memory store for Approval records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> approval_id -> Approval
        self._approvals: dict[str, dict[str, Approval]] = {}

    def get(self, tenant_id: str, approval_id: str) -> Approval | None:
        with self._lock:
            return self._approvals.get(tenant_id, {}).get(approval_id)

    def save(self, tenant_id: str, approval: Approval) -> None:
        if approval.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Approval tenant '{approval.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._approvals.setdefault(tenant_id, {})
            tenant_store[approval.approval_id] = approval

    def list_for_action(self, tenant_id: str, action_id: str) -> list[Approval]:
        with self._lock:
            tenant_store = self._approvals.get(tenant_id, {})
            return [appr for appr in tenant_store.values() if appr.binding.action_id == action_id]

    def list_pending(self, tenant_id: str | None = None) -> list[Approval]:
        """List all pending approval requests within a tenant partition (or all tenants if None)."""
        with self._lock:
            if tenant_id is not None:
                tenant_store = self._approvals.get(tenant_id, {})
                return [appr for appr in tenant_store.values() if appr.is_pending]
            return [
                appr
                for tenant_store in self._approvals.values()
                for appr in tenant_store.values()
                if appr.is_pending
            ]

    def atomic_consume(self, tenant_id: str, approval_id: str) -> Approval:
        """Atomically verify and transition approval to CONSUMED under lock (§20, §177 Vector 10)."""
        with self._lock:
            tenant_store = self._approvals.setdefault(tenant_id, {})
            approval = tenant_store.get(approval_id)
            if approval is None:
                raise InvalidCommerceError(
                    f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
                )
            if approval.status == ApprovalStatus.CONSUMED:
                raise ApprovalAlreadyConsumedError(
                    f"Approval '{approval_id}' has already been consumed"
                )
            if not approval.is_approved:
                raise ApprovalBindingMismatchError(
                    f"Approval is in status '{approval.status.value}', not APPROVED"
                )
            consumed = approval.consume()
            tenant_store[approval_id] = consumed
            return consumed

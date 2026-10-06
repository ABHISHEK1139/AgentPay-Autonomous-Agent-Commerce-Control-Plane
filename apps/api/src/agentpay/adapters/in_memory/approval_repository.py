"""Thread-safe, tenant-partitioned in-memory approval repository (§20, §49, §50).

Enforces:
- Explicit multi-tenant data partitioning.
- Strict isolation preventing cross-tenant access.
"""

from __future__ import annotations

import threading

from agentpay.application.ports.approval_port import ApprovalRepository
from agentpay.domain.approval import Approval
from agentpay.domain.errors import CrossTenantAccessError


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

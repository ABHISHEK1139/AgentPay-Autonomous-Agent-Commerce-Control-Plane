"""Application service for human approval workflows and exact binding verification (§20, §66).

Enforces:
- Strict binding of approvals to exact action parameters (§0.4, §20).
- Lifecycle state machine transitions for approvals.
- Multi-tenant boundary enforcement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agentpay.application.ports.approval_port import ApprovalRepository
from agentpay.domain.action_request import ActionRequest
from agentpay.domain.approval import Approval, ApprovalBinding
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidApprovalError,
)


class ApprovalService:
    """Orchestrates approval generation, verification, and transition lifecycles."""

    def __init__(self, approval_repo: ApprovalRepository | None = None) -> None:
        self._repo = approval_repo

    def create_approval(
        self,
        action: ActionRequest,
        tenant_id: str,
        reason: str | None = None,
        requested_at: datetime | None = None,
    ) -> Approval:
        """Create a pending approval request strictly bound to the action request (§20)."""
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest belongs to a different tenant")

        if action.amount is None:
            raise InvalidApprovalError("Cannot create approval for action without amount")

        merchant_id = action.resource.id
        resource_id = action.resource.id

        binding = ApprovalBinding(
            action_id=action.action_id,
            amount=action.amount,
            merchant_id=merchant_id,
            resource_id=resource_id,
            currency=action.amount.currency,
            action_version=action.version,
            tenant_id=tenant_id,
            principal_id=action.principal_id,
        )

        approval_id = f"appr_{uuid.uuid4().hex[:16]}"
        approval = Approval(
            approval_id=approval_id,
            tenant_id=tenant_id,
            binding=binding,
            requested_at=requested_at or datetime.now(UTC),
            reason=reason,
        )

        if self._repo is not None:
            self._repo.save(tenant_id, approval)

        return approval

    # Alias for specification consistency
    create_approval_request = create_approval

    def verify_binding(self, approval: Approval, action: ActionRequest) -> bool:
        """Strictly verify that an approval binds to the action request being executed (§20, §0.4).

        Raises:
            ApprovalBindingMismatchError: If any parameter does not match exactly.
        """
        if action.amount is None:
            raise ApprovalBindingMismatchError("Action does not specify an amount")

        approval.binding.assert_exact_match(
            action_id=action.action_id,
            amount=action.amount,
            merchant_id=action.resource.id,
            resource_id=action.resource.id,
            currency=action.amount.currency,
            action_version=action.version,
            tenant_id=action.tenant_id,
            principal_id=action.principal_id,
        )
        return True

    # Alias for specification consistency
    verify_approval_binding = verify_binding

    def grant_approval(
        self,
        approval_id: str,
        approver_id: str,
        tenant_id: str,
        decided_at: datetime | None = None,
    ) -> Approval:
        """Grant a pending approval request."""
        if self._repo is None:
            raise InvalidApprovalError("Repository not configured in ApprovalService")

        approval = self._repo.get(tenant_id, approval_id)
        if approval is None:
            raise InvalidApprovalError(
                f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
            )

        updated = approval.approve(approver_id, decided_at)
        self._repo.save(tenant_id, updated)
        return updated

    def reject_approval(
        self,
        approval_id: str,
        approver_id: str,
        reason: str,
        tenant_id: str,
        decided_at: datetime | None = None,
    ) -> Approval:
        """Reject a pending approval request."""
        if self._repo is None:
            raise InvalidApprovalError("Repository not configured in ApprovalService")

        approval = self._repo.get(tenant_id, approval_id)
        if approval is None:
            raise InvalidApprovalError(
                f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
            )

        updated = approval.reject(approver_id, reason, decided_at)
        self._repo.save(tenant_id, updated)
        return updated

    def consume_approval(
        self,
        approval_id: str,
        tenant_id: str,
        consumed_at: datetime | None = None,
    ) -> Approval:
        """Mark an approved request as consumed upon execution (§20)."""
        if self._repo is None:
            raise InvalidApprovalError("Repository not configured in ApprovalService")

        approval = self._repo.get(tenant_id, approval_id)
        if approval is None:
            raise InvalidApprovalError(
                f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
            )

        updated = approval.consume(consumed_at)
        self._repo.save(tenant_id, updated)
        return updated

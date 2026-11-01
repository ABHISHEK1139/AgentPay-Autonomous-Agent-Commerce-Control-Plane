"""Application service for human approval workflows and exact binding verification (§20, §66).

Enforces:
- Strict binding of approvals to exact action parameters (§0.4, §20).
- Lifecycle state machine transitions for approvals.
- Multi-tenant boundary enforcement.
- Human-approver authorization: only ADMIN, AGENT_OPERATOR, or FINANCE may decide,
  the deciding principal must be the authenticated caller, and self-approval is denied.
- Optional amount-tier routing via ``ApprovalPolicy`` (§20).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from agentpay.application.ports.approval_port import ApprovalRepository
from agentpay.domain.action_request import ActionRequest
from agentpay.domain.approval import Approval, ApprovalBinding, ApprovalPolicy, ApprovalStatus
from agentpay.domain.errors import (
    ApprovalAuthorizationError,
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidApprovalError,
)
from agentpay.domain.identity import UserRole

#: Roles permitted to grant or reject a human approval (§16, §20, §66).
APPROVER_ROLES: Final[frozenset[UserRole]] = frozenset(
    {UserRole.ADMIN, UserRole.AGENT_OPERATOR, UserRole.FINANCE}
)


class ApprovalService:
    """Orchestrates approval generation, verification, and transition lifecycles."""

    def __init__(
        self,
        approval_repo: ApprovalRepository | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self._repo = approval_repo
        self._approval_policy = approval_policy

    def create_approval(
        self,
        action: ActionRequest,
        tenant_id: str,
        reason: str | None = None,
        requested_at: datetime | None = None,
        merchant_id: str | None = None,
    ) -> Approval:
        """Create a pending approval request strictly bound to the action request (§20)."""
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest belongs to a different tenant")

        if action.amount is None:
            raise InvalidApprovalError("Cannot create approval for action without amount")

        # Bind the true merchant (resolved variant→product→merchant by the caller);
        # fall back to the resource id only when the merchant is unknown.
        bound_merchant_id = merchant_id or action.resource.id
        resource_id = action.resource.id

        binding = ApprovalBinding(
            action_id=action.action_id,
            amount=action.amount,
            merchant_id=bound_merchant_id,
            resource_id=resource_id,
            currency=action.amount.currency,
            action_version=action.version,
            tenant_id=tenant_id,
            agent_id=action.agent_id,
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

    def verify_binding(
        self,
        approval: Approval,
        action: ActionRequest,
        merchant_id: str | None = None,
    ) -> bool:
        """Strictly verify that an approval binds to the action request being executed (§20, §0.4).

        `merchant_id` is the merchant resolved from the action's variant→product→merchant
        chain by the caller. When it cannot be resolved, the resource id is used, matching
        the fallback applied at approval-creation time.

        Raises:
            ApprovalBindingMismatchError: If any parameter does not match exactly.
        """
        if action.amount is None:
            raise ApprovalBindingMismatchError("Action does not specify an amount")

        approval.binding.assert_exact_match(
            action_id=action.action_id,
            amount=action.amount,
            merchant_id=merchant_id or action.resource.id,
            resource_id=action.resource.id,
            currency=action.amount.currency,
            action_version=action.version,
            tenant_id=action.tenant_id,
            agent_id=action.agent_id,
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
        *,
        approver_role: UserRole | None,
        caller_principal_id: str,
    ) -> Approval:
        """Grant a pending approval request after authorizing the human approver (§20, §66)."""
        approval = self._get_or_raise(tenant_id, approval_id)
        self._authorize_decision(
            approval,
            approver_id=approver_id,
            approver_role=approver_role,
            caller_principal_id=caller_principal_id,
        )

        updated = approval.approve(approver_id, decided_at)
        self._repo_save(tenant_id, updated, expected_status=approval.status)
        return updated

    def reject_approval(
        self,
        approval_id: str,
        approver_id: str,
        reason: str,
        tenant_id: str,
        decided_at: datetime | None = None,
        *,
        approver_role: UserRole | None,
        caller_principal_id: str,
    ) -> Approval:
        """Reject a pending approval request after authorizing the human approver (§20, §66)."""
        approval = self._get_or_raise(tenant_id, approval_id)
        self._authorize_decision(
            approval,
            approver_id=approver_id,
            approver_role=approver_role,
            caller_principal_id=caller_principal_id,
        )

        updated = approval.reject(approver_id, reason, decided_at)
        self._repo_save(tenant_id, updated, expected_status=approval.status)
        return updated

    def consume_approval(
        self,
        approval_id: str,
        tenant_id: str,
        consumed_at: datetime | None = None,
    ) -> Approval:
        """Mark an approved request as consumed upon execution (§20)."""
        approval = self._get_or_raise(tenant_id, approval_id)
        updated = approval.consume(consumed_at)
        self._repo_save(tenant_id, updated, expected_status=approval.status)
        return updated

    def _repo_save(
        self,
        tenant_id: str,
        approval: Approval,
        expected_status: ApprovalStatus | None = None,
    ) -> None:
        if self._repo is None:
            raise InvalidApprovalError("Repository not configured in ApprovalService")
        self._repo.save(tenant_id, approval, expected_status=expected_status)

    def _get_or_raise(self, tenant_id: str, approval_id: str) -> Approval:
        if self._repo is None:
            raise InvalidApprovalError("Repository not configured in ApprovalService")
        approval = self._repo.get(tenant_id, approval_id)
        if approval is None:
            raise InvalidApprovalError(
                f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
            )
        return approval

    def _authorize_decision(
        self,
        approval: Approval,
        *,
        approver_id: str,
        approver_role: UserRole | None,
        caller_principal_id: str,
    ) -> None:
        """Deterministically authorize a human decision on an approval (§2.1, §16, §20)."""
        if approver_role not in APPROVER_ROLES:
            raise ApprovalAuthorizationError(
                f"Role '{approver_role.value if approver_role else 'agent'}' cannot decide approvals; "
                f"required one of {sorted(role.value for role in APPROVER_ROLES)}"
            )

        if not caller_principal_id or approver_id != caller_principal_id:
            raise ApprovalAuthorizationError(
                "Approver identity must match the authenticated principal"
            )

        if approver_id == approval.binding.principal_id:
            raise ApprovalAuthorizationError(
                "Self-approval is prohibited: the requesting principal cannot approve its own action"
            )

        if self._approval_policy is not None:
            tier = self._approval_policy.required_tier(approval.binding.amount.minor_units)
            if tier is not None:
                try:
                    required_role = UserRole(tier.approver_role)
                except ValueError as err:
                    raise InvalidApprovalError(
                        f"Approval policy references unknown role '{tier.approver_role}'"
                    ) from err
                # ADMIN is the tenant superuser and may act on any tier; every other
                # approver must hold the exact role the policy requires.
                if approver_role is not required_role and approver_role is not UserRole.ADMIN:
                    raise ApprovalAuthorizationError(
                        f"Amount {approval.binding.amount} requires approver role "
                        f"'{required_role.value}', got '{approver_role.value}'"
                    )

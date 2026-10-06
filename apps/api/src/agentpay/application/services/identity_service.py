"""Application service for agent authorization, tenant boundary enforcement, and capability validation.

Enforces deterministic authorization rules below the policy boundary (§2, §16, §18, §134).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from agentpay.application.ports.identity_port import (
    AgentPermissionsRepository,
    AgentRepository,
    TenantRepository,
)
from agentpay.domain.action_request import ActionRequest
from agentpay.domain.errors import (
    AgentExpiredError,
    AgentNotActiveError,
    AgentQuarantinedError,
    AgentRevokedError,
    AgentSuspendedError,
    CrossTenantAccessError,
    DomainError,
    InvalidIdentityError,
    MissingCapabilityError,
    PrivilegeEscalationAttemptError,
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
)

# Default mapping from ActionRequest action/domain to required capability
DEFAULT_CAPABILITY_ACTION_MAP: Final[dict[str, str]] = {
    "purchase": "payment.request",
    "checkout": "checkout.prepare",
    "cart": "cart.create",
    "search": "catalog.search",
    "catalog_search": "catalog.search",
    "compare": "catalog.compare",
}

# Administrative actions forbidden to agents to prevent privilege escalation
FORBIDDEN_AGENT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "update_permissions",
        "grant_capability",
        "elevate_role",
        "update_limits",
        "revoke_limit",
        "modify_policy",
        "register_agent",
        "widen_spending_limits",
    }
)


class AgentAuthorizationService:
    """Evaluates agent authorization, tenant boundaries, lifecycle status, and capabilities."""

    def __init__(
        self,
        tenant_repo: TenantRepository | None = None,
        agent_repo: AgentRepository | None = None,
        permissions_repo: AgentPermissionsRepository | None = None,
        capability_action_map: dict[str, str] | None = None,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._agent_repo = agent_repo
        self._permissions_repo = permissions_repo
        self._capability_map = capability_action_map or DEFAULT_CAPABILITY_ACTION_MAP

    def validate_agent_active(self, agent: Agent, now: datetime | None = None) -> None:
        """Validate that an agent is in ACTIVE status and not expired (§15).

        Raises:
            AgentRevokedError: If agent is REVOKED (terminal state).
            AgentSuspendedError: If agent is SUSPENDED.
            AgentQuarantinedError: If agent is QUARANTINED.
            AgentExpiredError: If agent is EXPIRED or past expires_at.
            AgentNotActiveError: If agent is otherwise not ACTIVE.
        """
        if agent.status is AgentStatus.REVOKED:
            raise AgentRevokedError(f"Agent '{agent.agent_id}' is permanently REVOKED")

        if agent.status is AgentStatus.SUSPENDED:
            raise AgentSuspendedError(f"Agent '{agent.agent_id}' is SUSPENDED")

        if agent.status is AgentStatus.QUARANTINED:
            raise AgentQuarantinedError(f"Agent '{agent.agent_id}' is QUARANTINED")

        if agent.status is AgentStatus.EXPIRED:
            raise AgentExpiredError(f"Agent '{agent.agent_id}' is EXPIRED")

        if agent.status is not AgentStatus.ACTIVE:
            raise AgentNotActiveError(
                f"Agent '{agent.agent_id}' is not ACTIVE (current status: {agent.status.value})"
            )

        current_time = now if now is not None else datetime.now(UTC)
        if current_time.tzinfo is None:
            raise InvalidIdentityError("Comparison timestamp must be timezone-aware (UTC)")

        if current_time < agent.created_at:
            raise AgentNotActiveError(
                f"Agent '{agent.agent_id}' is not yet active (created_at: {agent.created_at.isoformat()})"
            )

        if current_time > agent.expires_at:
            raise AgentExpiredError(
                f"Agent '{agent.agent_id}' expired at {agent.expires_at.isoformat()}"
            )

    def check_capability(self, agent: Agent, capability: str) -> bool:
        """Check whether an agent holds a specific capability (§16, §17)."""
        return capability in agent.capabilities

    def resolve_required_capability(self, action: ActionRequest) -> str:
        """Resolve the required capability for an action request."""
        # 1. Exact action match
        if action.action in self._capability_map:
            return self._capability_map[action.action]
        # 2. Domain-qualified match (domain.action)
        qualified = f"{action.domain}.{action.action}"
        if qualified in self._capability_map:
            return self._capability_map[qualified]
        # 3. Fallback convention: domain.action
        return qualified

    def authorize_action(
        self,
        agent: Agent,
        action: ActionRequest,
        tenant_id: str,
        permissions: AgentPermissions | None = None,
        now: datetime | None = None,
        raise_on_denial: bool = True,
    ) -> bool:
        """Deterministically authorize an action requested by an agent.

        Enforces:
        1. Multi-tenant isolation: execution tenant_id, agent.tenant_id, and action.tenant_id must match.
        2. Agent identity binding: action.agent_id must match agent.agent_id.
        3. Privilege escalation defense: rejects actions attempting to elevate permissions or limits.
        4. Lifecycle validity: agent must be active and unexpired.
        5. Capability requirement: agent must possess the capability required for the action.

        Args:
            agent: The requesting Agent entity.
            action: The canonical ActionRequest.
            tenant_id: The execution tenant context.
            permissions: Optional explicit AgentPermissions override.
            now: Optional current timestamp for expiration evaluation.
            raise_on_denial: If True, raises MissingCapabilityError when missing capability.

        Returns:
            bool: True if authorized, False if agent lacks required capability and raise_on_denial is False.

        Raises:
            CrossTenantAccessError: On any tenant_id or agent_id mismatch.
            PrivilegeEscalationAttemptError: If agent attempts self-escalation.
            AgentRevokedError: If agent is revoked.
            AgentNotActiveError: If agent is inactive or expired.
            MissingCapabilityError: If agent lacks required capability and raise_on_denial is True.
        """
        # 1. Multi-Tenant Isolation (§49, §50)
        if agent.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                "Agent tenant does not match context: Context tenant_id mismatch"
            )
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest belongs to different tenant")

        # 2. Agent Identity Binding (§6, §15)
        if action.agent_id != agent.agent_id:
            raise CrossTenantAccessError("Action agent_id does not match requesting agent")
        if action.principal_id != agent.principal_id:
            raise InvalidIdentityError(
                f"Action principal_id '{action.principal_id}' does not match agent principal_id '{agent.principal_id}'"
            )

        # 3. Privilege Escalation Defense (§0.4, §15, §112)
        normalized_action = action.action.strip().lower()
        if normalized_action in FORBIDDEN_AGENT_ACTIONS:
            raise PrivilegeEscalationAttemptError(
                f"Agent '{agent.agent_id}' cannot perform administrative action '{action.action}'"
            )

        # 4. Agent Lifecycle & Expiration (§15)
        self.validate_agent_active(agent, now=now)

        # 5. Permissions Resolution and Escalation Defense (§17, §49, §50)
        if permissions is None and self._permissions_repo is not None:
            permissions = self._permissions_repo.get(tenant_id, agent.agent_id)

        effective_caps: tuple[str, ...] = agent.capabilities
        if permissions is not None:
            if permissions.tenant_id != tenant_id:
                raise CrossTenantAccessError("Permissions tenant does not match execution context")
            if permissions.agent_id != agent.agent_id:
                raise InvalidIdentityError(
                    f"Permissions agent_id '{permissions.agent_id}' does not match agent '{agent.agent_id}'"
                )

            # Prevent unverified external permissions escalation
            if self._permissions_repo is not None:
                stored_perms = self._permissions_repo.get(tenant_id, agent.agent_id)
                if stored_perms is not None:
                    stored_perms.assert_no_escalation(permissions)
                    baseline_caps = set(stored_perms.capabilities)
                else:
                    baseline_caps = set(agent.capabilities)
            else:
                baseline_caps = set(agent.capabilities)

            added_caps = set(permissions.capabilities) - baseline_caps
            if added_caps:
                raise PrivilegeEscalationAttemptError(
                    f"Permissions cannot widen capabilities beyond registered baseline: added {sorted(added_caps)}"
                )
            effective_caps = permissions.capabilities

        # 6. Capability Evaluation (§16, §17)
        required_cap = self.resolve_required_capability(action)

        if required_cap in effective_caps:
            return True

        if raise_on_denial:
            raise MissingCapabilityError(
                f"Agent '{agent.agent_id}' lacks required capability '{required_cap}'"
            )
        return False

    def validate_category_allowed(
        self,
        agent: Agent,
        permissions: AgentPermissions,
        action: ActionRequest,
    ) -> bool:
        """Validate that the action's target category is permitted under AgentPermissions (§17)."""
        category = ""
        if ":" in action.resource.type:
            category = action.resource.type.split(":", 1)[1]
        else:
            category = action.resource.type

        if not permissions.allows_category(category):
            raise DomainError(
                f"Category '{category}' is not permitted for agent '{agent.agent_id}'"
            )
        return True

    def update_agent_permissions(
        self,
        tenant_id: str,
        agent_id: str,
        permissions: AgentPermissions,
        caller_principal_id: str,
        caller_is_agent: bool,
    ) -> None:
        """Update agent permissions with strict privilege escalation defense (§0.4, §15, §17).

        Only human operators can update agent permissions. Agents attempting self-escalation
        or cross-agent escalation are rejected with PrivilegeEscalationAttemptError.
        """
        if caller_is_agent:
            raise PrivilegeEscalationAttemptError("Agent cannot alter permissions")

        if permissions.tenant_id != tenant_id:
            raise CrossTenantAccessError("Permissions tenant does not match context tenant")

        if permissions.agent_id != agent_id:
            raise InvalidIdentityError(
                f"Permissions agent_id '{permissions.agent_id}' does not match agent '{agent_id}'"
            )

        if self._permissions_repo is not None:
            self._permissions_repo.save(tenant_id, permissions)

    def prevent_self_escalation(
        self,
        current_permissions: AgentPermissions,
        proposed_permissions: AgentPermissions,
    ) -> None:
        """Prevent an agent from widening its own capabilities or spending ceilings (§0.4, §15, §112)."""
        current_permissions.assert_no_escalation(proposed_permissions)

"""Pure domain models and state machines for Identity, Tenancy, and Agent Authorization.

Following Clean Architecture (§8, §10):
- Pure Python standard library and internal domain concepts only.
- Zero imports of external web frameworks, ORMs, or third-party libraries.
- Strict multi-tenant isolation, explicit boundaries, and fail-closed security.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentpay.domain.action_request import ActionRequest  # noqa: F401 (re-export/typing)
from agentpay.domain.errors import (
    AgentRevokedError,
    InvalidAgentStatusTransitionError,
    InvalidIdentityError,
    PrivilegeEscalationAttemptError,
)
from agentpay.domain.policy import SpendingLimits


class TenantStatus(StrEnum):
    """Lifecycle state of an organizational tenant (§49, §50)."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class UserRole(StrEnum):
    """Coarse RBAC roles assigned to human principals (§16)."""

    ADMIN = "ADMIN"
    MERCHANT_ADMIN = "MERCHANT_ADMIN"
    FINANCE = "FINANCE"
    BUYER = "BUYER"
    AGENT_OPERATOR = "AGENT_OPERATOR"
    AUDITOR = "AUDITOR"


class UserStatus(StrEnum):
    """Lifecycle state of a human user within a tenant."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class AgentStatus(StrEnum):
    """Lifecycle states of an autonomous agent (§15)."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"


VALID_AGENT_STATUS_TRANSITIONS: Final[dict[AgentStatus, frozenset[AgentStatus]]] = {
    AgentStatus.ACTIVE: frozenset(
        {
            AgentStatus.SUSPENDED,
            AgentStatus.REVOKED,
            AgentStatus.EXPIRED,
            AgentStatus.QUARANTINED,
        }
    ),
    AgentStatus.SUSPENDED: frozenset(
        {
            AgentStatus.ACTIVE,
            AgentStatus.REVOKED,
            AgentStatus.EXPIRED,
        }
    ),
    AgentStatus.QUARANTINED: frozenset(
        {
            AgentStatus.SUSPENDED,
            AgentStatus.REVOKED,
        }
    ),
    AgentStatus.EXPIRED: frozenset(
        {
            AgentStatus.REVOKED,
        }
    ),
    # REVOKED is terminal: no transitions allowed out of REVOKED
    AgentStatus.REVOKED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Tenant:
    """An isolated multi-tenant organization boundary (§49, §50)."""

    tenant_id: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE

    def __post_init__(self) -> None:
        identifiers: tuple[tuple[str, object], ...] = (
            ("tenant_id", self.tenant_id),
            ("name", self.name),
        )
        for field_name, value in identifiers:
            if not isinstance(value, str) or not value.strip():
                raise InvalidIdentityError(f"{field_name} must be a non-empty string")

        status_val: object = self.status
        if isinstance(status_val, str) and not isinstance(status_val, TenantStatus):
            try:
                object.__setattr__(self, "status", TenantStatus(status_val))
            except ValueError as err:
                raise InvalidIdentityError(
                    f"status must be a TenantStatus, got {status_val!r}"
                ) from err
        elif not isinstance(status_val, TenantStatus):
            raise InvalidIdentityError(f"status must be a TenantStatus, got {status_val!r}")

    @property
    def is_active(self) -> bool:
        return self.status is TenantStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class User:
    """A human principal within a tenant organization (§16)."""

    user_id: str
    tenant_id: str
    role: UserRole
    email: str
    status: UserStatus = UserStatus.ACTIVE

    def __post_init__(self) -> None:
        identifiers: tuple[tuple[str, object], ...] = (
            ("user_id", self.user_id),
            ("tenant_id", self.tenant_id),
            ("email", self.email),
        )
        for field_name, value in identifiers:
            if not isinstance(value, str) or not value.strip():
                raise InvalidIdentityError(f"{field_name} must be a non-empty string")

        role_val: object = self.role
        if isinstance(role_val, str) and not isinstance(role_val, UserRole):
            try:
                object.__setattr__(self, "role", UserRole(role_val))
            except ValueError as err:
                raise InvalidIdentityError(f"role must be a UserRole, got {role_val!r}") from err
        elif not isinstance(role_val, UserRole):
            raise InvalidIdentityError(f"role must be a UserRole, got {role_val!r}")

        if "@" not in self.email:
            raise InvalidIdentityError(f"email {self.email!r} is not a valid email address")
        email_parts = self.email.split("@")
        if (
            len(email_parts) != 2
            or not email_parts[0]
            or "." not in email_parts[1]
            or not email_parts[1].split(".")[-1]
        ):
            raise InvalidIdentityError(f"email {self.email!r} is not a valid email address")

        status_val: object = self.status
        if isinstance(status_val, str) and not isinstance(status_val, UserStatus):
            try:
                object.__setattr__(self, "status", UserStatus(status_val))
            except ValueError as err:
                raise InvalidIdentityError(
                    f"status must be a UserStatus, got {status_val!r}"
                ) from err
        elif not isinstance(status_val, UserStatus):
            raise InvalidIdentityError(f"status must be a UserStatus, got {status_val!r}")

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Agent:
    """An autonomous software agent identity bound to a tenant and principal (§15)."""

    agent_id: str
    tenant_id: str
    principal_id: str
    issuer: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    status: AgentStatus = AgentStatus.ACTIVE

    def __post_init__(self) -> None:
        identifiers: tuple[tuple[str, object], ...] = (
            ("agent_id", self.agent_id),
            ("tenant_id", self.tenant_id),
            ("principal_id", self.principal_id),
            ("issuer", self.issuer),
        )
        for field_name, value in identifiers:
            if not isinstance(value, str) or not value.strip():
                raise InvalidIdentityError(f"{field_name} must be a non-empty string")

        caps_val: object = self.capabilities
        if isinstance(caps_val, list):
            object.__setattr__(self, "capabilities", tuple(caps_val))
        elif not isinstance(caps_val, tuple):
            raise InvalidIdentityError(
                f"capabilities must be a tuple[str, ...], got {type(caps_val).__name__}"
            )
        for cap in self.capabilities:
            cap_val: object = cap
            if not isinstance(cap_val, str) or not cap_val.strip():
                raise InvalidIdentityError("capability items must be non-empty strings")

        scopes_val: object = self.scopes
        if isinstance(scopes_val, list):
            object.__setattr__(self, "scopes", tuple(scopes_val))
        elif not isinstance(scopes_val, tuple):
            raise InvalidIdentityError(
                f"scopes must be a tuple[str, ...], got {type(scopes_val).__name__}"
            )
        for scope in self.scopes:
            scope_val: object = scope
            if not isinstance(scope_val, str) or not scope_val.strip():
                raise InvalidIdentityError("scope items must be non-empty strings")

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidIdentityError("created_at must be timezone-aware (UTC)")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise InvalidIdentityError("expires_at must be timezone-aware (UTC)")
        if self.expires_at <= self.created_at:
            raise InvalidIdentityError(
                f"expires_at must be strictly after created_at "
                f"({self.expires_at.isoformat()} <= {self.created_at.isoformat()})"
            )

        agent_status_val: object = self.status
        if isinstance(agent_status_val, str) and not isinstance(agent_status_val, AgentStatus):
            try:
                object.__setattr__(self, "status", AgentStatus(agent_status_val))
            except ValueError as err:
                raise InvalidIdentityError(
                    f"status must be an AgentStatus, got {agent_status_val!r}"
                ) from err
        elif not isinstance(agent_status_val, AgentStatus):
            raise InvalidIdentityError(f"status must be an AgentStatus, got {agent_status_val!r}")

    @property
    def is_active(self) -> bool:
        return self.status is AgentStatus.ACTIVE

    @property
    def is_revoked(self) -> bool:
        return self.status is AgentStatus.REVOKED

    def is_expired(self, at: datetime | None = None) -> bool:
        if self.status is AgentStatus.EXPIRED:
            return True
        if at is None:
            at = datetime.now(UTC)
        elif at.tzinfo is None:
            raise InvalidIdentityError("comparison timestamp must be timezone-aware")
        return at > self.expires_at

    def is_active_at(self, at: datetime) -> bool:
        if at.tzinfo is None:
            raise InvalidIdentityError("comparison timestamp must be timezone-aware")
        if self.status is not AgentStatus.ACTIVE:
            return False
        return self.created_at <= at <= self.expires_at

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def transition_to(self, new_status: AgentStatus) -> Agent:
        """Create a new Agent instance with updated status following state machine rules."""
        target_val: object = new_status
        if isinstance(target_val, str) and not isinstance(target_val, AgentStatus):
            try:
                new_status = AgentStatus(target_val)
            except ValueError as err:
                raise InvalidIdentityError(
                    f"target status must be an AgentStatus, got {target_val!r}"
                ) from err
        elif not isinstance(target_val, AgentStatus):
            raise InvalidIdentityError(f"target status must be an AgentStatus, got {target_val!r}")

        if self.status is AgentStatus.REVOKED:
            raise AgentRevokedError(
                f"Cannot transition from terminal REVOKED agent '{self.agent_id}' to {new_status.value}"
            )
        if self.status is AgentStatus.QUARANTINED and new_status is AgentStatus.ACTIVE:
            raise InvalidIdentityError(
                f"Cannot transition QUARANTINED directly to ACTIVE for agent '{self.agent_id}'"
            )
        valid_targets = VALID_AGENT_STATUS_TRANSITIONS.get(self.status, frozenset())
        if new_status not in valid_targets:
            raise InvalidAgentStatusTransitionError(
                f"Invalid agent status transition from {self.status.value} to {new_status.value}"
            )
        return Agent(
            agent_id=self.agent_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            issuer=self.issuer,
            capabilities=self.capabilities,
            scopes=self.scopes,
            created_at=self.created_at,
            expires_at=self.expires_at,
            status=new_status,
        )


@dataclass(frozen=True, slots=True)
class AgentPermissions:
    """Explicit, versioned, revocable capability and spending grants for an agent (§17)."""

    agent_id: str
    tenant_id: str
    capabilities: tuple[str, ...]
    limits: SpendingLimits
    allowed_categories: tuple[str, ...] = field(default_factory=tuple)
    requires_approval_above_minor: int | None = None
    version: int = 1

    def __post_init__(self) -> None:
        agent_id_val: object = self.agent_id
        if not isinstance(agent_id_val, str) or not agent_id_val.strip():
            raise InvalidIdentityError("agent_id must be a non-empty string")

        tenant_id_val: object = self.tenant_id
        if not isinstance(tenant_id_val, str) or not tenant_id_val.strip():
            raise InvalidIdentityError("tenant_id must be a non-empty string")

        caps_val: object = self.capabilities
        if isinstance(caps_val, list):
            object.__setattr__(self, "capabilities", tuple(caps_val))
        elif not isinstance(caps_val, tuple):
            raise InvalidIdentityError(
                f"capabilities must be a tuple[str, ...], got {type(caps_val).__name__}"
            )
        for cap in self.capabilities:
            cap_val: object = cap
            if not isinstance(cap_val, str) or not cap_val.strip():
                raise InvalidIdentityError("capabilities must contain non-empty strings")

        limits_val: object = self.limits
        if not isinstance(limits_val, SpendingLimits):
            raise InvalidIdentityError(
                f"limits must be a SpendingLimits instance, got {type(limits_val).__name__}"
            )

        allowed_cats_val: object = self.allowed_categories
        if isinstance(allowed_cats_val, list):
            object.__setattr__(self, "allowed_categories", tuple(allowed_cats_val))
        elif not isinstance(allowed_cats_val, tuple):
            raise InvalidIdentityError(
                f"allowed_categories must be a tuple[str, ...], got {type(allowed_cats_val).__name__}"
            )
        for cat in self.allowed_categories:
            cat_val: object = cat
            if not isinstance(cat_val, str) or not cat_val.strip():
                raise InvalidIdentityError("allowed_categories must contain non-empty strings")

        approval_above: object = self.requires_approval_above_minor
        if approval_above is not None and (
            isinstance(approval_above, bool)
            or not isinstance(approval_above, int)
            or approval_above < 0
        ):
            raise InvalidIdentityError(
                f"requires_approval_above_minor must be None or a non-negative int, got {approval_above!r}"
            )

        version_val: object = self.version
        if isinstance(version_val, bool) or not isinstance(version_val, int) or version_val < 1:
            raise InvalidIdentityError(f"version must be a positive int, got {version_val!r}")

    def allows_category(self, category: str) -> bool:
        """Check if a category is permitted. If allowed_categories is empty, all categories are permitted."""
        if not self.allowed_categories:
            return True
        return category.strip().lower() in {c.strip().lower() for c in self.allowed_categories}

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def effective_approval_threshold_minor(self) -> int | None:
        """Resolve the effective threshold between explicit field and limits object."""
        if self.requires_approval_above_minor is not None:
            return self.requires_approval_above_minor
        return self.limits.requires_approval_above_minor

    def assert_no_escalation(self, proposed: AgentPermissions) -> None:
        """Verify proposed permissions do not widen capabilities or spending limits (§0.4, §15, §112)."""
        current_caps = set(self.capabilities)
        proposed_caps = set(proposed.capabilities)
        added_caps = proposed_caps - current_caps
        if added_caps:
            raise PrivilegeEscalationAttemptError(
                f"Cannot widen capabilities: added {sorted(added_caps)}"
            )

        cur_per_tx = self.limits.per_transaction_minor
        prop_per_tx = proposed.limits.per_transaction_minor
        if prop_per_tx is not None and (cur_per_tx is not None and prop_per_tx > cur_per_tx):
            raise PrivilegeEscalationAttemptError(
                f"Cannot increase per_transaction_minor from {cur_per_tx} to {prop_per_tx}"
            )
        if cur_per_tx is not None and prop_per_tx is None:
            raise PrivilegeEscalationAttemptError("Cannot remove per_transaction_minor ceiling")

        cur_daily = self.limits.daily_minor
        prop_daily = proposed.limits.daily_minor
        if prop_daily is not None and (cur_daily is not None and prop_daily > cur_daily):
            raise PrivilegeEscalationAttemptError(
                f"Cannot increase daily_minor from {cur_daily} to {prop_daily}"
            )
        if cur_daily is not None and prop_daily is None:
            raise PrivilegeEscalationAttemptError("Cannot remove daily_minor ceiling")

        # Approval Threshold Escalation Defense (§0.4, §2.2)
        cur_thresh = self.effective_approval_threshold_minor()
        prop_thresh = proposed.effective_approval_threshold_minor()
        if cur_thresh is not None and prop_thresh is None:
            raise PrivilegeEscalationAttemptError("Cannot remove approval threshold")
        if cur_thresh is not None and prop_thresh is not None and prop_thresh > cur_thresh:
            raise PrivilegeEscalationAttemptError(
                f"Cannot increase approval threshold from {cur_thresh} to {prop_thresh}"
            )

        cur_lim_thresh = self.limits.requires_approval_above_minor
        prop_lim_thresh = proposed.limits.requires_approval_above_minor
        if cur_lim_thresh is not None and prop_lim_thresh is None:
            raise PrivilegeEscalationAttemptError(
                "Cannot remove limits.requires_approval_above_minor ceiling"
            )
        if (
            cur_lim_thresh is not None
            and prop_lim_thresh is not None
            and prop_lim_thresh > cur_lim_thresh
        ):
            raise PrivilegeEscalationAttemptError(
                f"Cannot increase limits.requires_approval_above_minor from {cur_lim_thresh} to {prop_lim_thresh}"
            )

        # Category Widening Defense (§0.4, §17)
        if self.allowed_categories:
            if not proposed.allowed_categories:
                raise PrivilegeEscalationAttemptError(
                    "Cannot remove allowed_categories restriction"
                )
            current_cats = {c.strip().lower() for c in self.allowed_categories}
            proposed_cats = {c.strip().lower() for c in proposed.allowed_categories}
            added_cats = proposed_cats - current_cats
            if added_cats:
                raise PrivilegeEscalationAttemptError(
                    f"Cannot widen allowed_categories: added {sorted(added_cats)}"
                )

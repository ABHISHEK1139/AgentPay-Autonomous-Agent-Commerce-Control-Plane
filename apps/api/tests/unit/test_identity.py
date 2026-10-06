"""Unit test suite for Milestone 1: Identity, Tenancy & Agent Authorization.

Verifies:
- Domain model immutability and invariant validations (Tenant, User, Agent, AgentPermissions).
- Multi-tenant data isolation and cross-tenant access rejection (§49, §50).
- Agent lifecycle state machine (ACTIVE, SUSPENDED, REVOKED, EXPIRED, QUARANTINED) (§15).
- Privilege escalation defense (agent self-escalation, limit widening) (§0.4, §15, §112).
- Capability authorization and ABAC category restrictions (§16, §17).
- Tenant-scoped cache key formatting and injection resistance (§51).
- Thread-safe repository concurrency.
- Property-based validation via Hypothesis (100 examples per property).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.adapters.in_memory.identity_repository import (
    InMemoryAgentPermissionsRepository,
    InMemoryAgentRepository,
    InMemoryTenantRepository,
    format_cache_key,
    format_tenant_cache_key,
)
from agentpay.application.services.identity_service import (
    FORBIDDEN_AGENT_ACTIONS,
    AgentAuthorizationService,
)
from agentpay.domain.action_request import (
    ActionRequest,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
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
    Tenant,
    TenantStatus,
    User,
    UserRole,
    UserStatus,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import SpendingLimits

# =============================================================================
# Test Fixtures & Builders
# =============================================================================


def build_tenant(**overrides: object) -> Tenant:
    defaults: dict[str, object] = {
        "tenant_id": "tenant_01",
        "name": "Acme Corp",
        "status": TenantStatus.ACTIVE,
    }
    return Tenant(**{**defaults, **overrides})  # type: ignore[arg-type]


def build_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "user_id": "user_01",
        "tenant_id": "tenant_01",
        "email": "operator@acme.com",
        "role": UserRole.AGENT_OPERATOR,
        "status": UserStatus.ACTIVE,
    }
    return User(**{**defaults, **overrides})  # type: ignore[arg-type]


def build_agent(**overrides: object) -> Agent:
    defaults: dict[str, object] = {
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "principal_id": "user_01",
        "issuer": "https://auth.acme.com",
        "capabilities": (
            "catalog.search",
            "catalog.compare",
            "cart.create",
            "checkout.prepare",
            "payment.request",
        ),
        "scopes": ("orders:write", "payments:write"),
        "status": AgentStatus.ACTIVE,
        "created_at": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
    }
    return Agent(**{**defaults, **overrides})  # type: ignore[arg-type]


def build_permissions(**overrides: object) -> AgentPermissions:
    defaults: dict[str, object] = {
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "capabilities": (
            "catalog.search",
            "catalog.compare",
            "cart.create",
            "checkout.prepare",
            "payment.request",
        ),
        "limits": SpendingLimits(
            per_transaction_minor=200_000,
            daily_minor=500_000,
            requires_approval_above_minor=200_000,
        ),
        "allowed_categories": ("electronics", "books"),
        "requires_approval_above_minor": 200_000,
        "version": 1,
    }
    return AgentPermissions(**{**defaults, **overrides})  # type: ignore[arg-type]


def build_action_request(**overrides: object) -> ActionRequest:
    defaults: dict[str, object] = {
        "action_id": "act_01",
        "tenant_id": "tenant_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "domain": "commerce",
        "action": "purchase",
        "resource": ResourceRef(type="product", id="prod_123"),
        "requested_at": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        "intent": Intent(purpose="procure office supplies"),
        "amount": Money(150_000, "INR"),
        "authorization_mode": AuthorizationMode.AUTONOMOUS,
        "version": 1,
    }
    return ActionRequest(**{**defaults, **overrides})  # type: ignore[arg-type]


# =============================================================================
# 1. Domain Model Immutability & Validation Tests
# =============================================================================


def test_tenant_valid_attributes() -> None:
    tenant = build_tenant()
    assert tenant.tenant_id == "tenant_01"
    assert tenant.name == "Acme Corp"
    assert tenant.status is TenantStatus.ACTIVE
    assert tenant.is_active is True


def test_tenant_immutability() -> None:
    tenant = build_tenant()
    with pytest.raises(FrozenInstanceError):
        tenant.name = "Modified Name"  # type: ignore[misc]


@pytest.mark.parametrize("invalid_id", ["", "   ", "\t\n"])
def test_tenant_rejects_empty_id(invalid_id: str) -> None:
    with pytest.raises(InvalidIdentityError, match="tenant_id"):
        build_tenant(tenant_id=invalid_id)


@pytest.mark.parametrize("invalid_name", ["", "   ", "\t\n"])
def test_tenant_rejects_empty_name(invalid_name: str) -> None:
    with pytest.raises(InvalidIdentityError, match="name"):
        build_tenant(name=invalid_name)


def test_user_valid_attributes() -> None:
    user = build_user()
    assert user.user_id == "user_01"
    assert user.tenant_id == "tenant_01"
    assert user.email == "operator@acme.com"
    assert user.role is UserRole.AGENT_OPERATOR
    assert user.status is UserStatus.ACTIVE
    assert user.is_active is True


def test_user_immutability() -> None:
    user = build_user()
    with pytest.raises(FrozenInstanceError):
        user.email = "new@acme.com"  # type: ignore[misc]


@pytest.mark.parametrize("invalid_email", ["", "invalid-email", "no_at_sign.com"])
def test_user_rejects_invalid_email(invalid_email: str) -> None:
    with pytest.raises(InvalidIdentityError, match="email"):
        build_user(email=invalid_email)


def test_agent_valid_attributes() -> None:
    agent = build_agent()
    assert agent.agent_id == "agent_01"
    assert agent.tenant_id == "tenant_01"
    assert agent.principal_id == "user_01"
    assert "payment.request" in agent.capabilities
    assert agent.status is AgentStatus.ACTIVE
    assert agent.is_active is True
    assert agent.is_revoked is False


def test_agent_immutability() -> None:
    agent = build_agent()
    with pytest.raises(FrozenInstanceError):
        agent.status = AgentStatus.SUSPENDED  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["agent_id", "tenant_id", "principal_id", "issuer"])
def test_agent_rejects_empty_identifiers(field_name: str) -> None:
    with pytest.raises(InvalidIdentityError):
        build_agent(**{field_name: "   "})


def test_agent_rejects_naive_created_at() -> None:
    with pytest.raises(InvalidIdentityError, match="timezone-aware"):
        build_agent(created_at=datetime(2026, 1, 1, 0, 0))  # noqa: DTZ001 - deliberately naive to prove rejection


def test_agent_rejects_naive_expires_at() -> None:
    with pytest.raises(InvalidIdentityError, match="timezone-aware"):
        build_agent(expires_at=datetime(2026, 12, 31, 0, 0))  # noqa: DTZ001 - deliberately naive to prove rejection


def test_agent_rejects_expiration_before_or_equal_to_creation() -> None:
    created = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(InvalidIdentityError, match="expires_at must be strictly after created_at"):
        build_agent(created_at=created, expires_at=created)
    with pytest.raises(InvalidIdentityError, match="expires_at must be strictly after created_at"):
        build_agent(created_at=created, expires_at=created - timedelta(days=1))


def test_agent_is_active_at_time_window_checks() -> None:
    created = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    expires = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    agent = build_agent(created_at=created, expires_at=expires, status=AgentStatus.ACTIVE)

    assert not agent.is_active_at(created - timedelta(seconds=1))
    assert agent.is_active_at(created)
    assert agent.is_active_at(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
    assert agent.is_active_at(expires)
    assert not agent.is_active_at(expires + timedelta(seconds=1))


def test_agent_permissions_immutability() -> None:
    perms = build_permissions()
    with pytest.raises(FrozenInstanceError):
        perms.version = 2  # type: ignore[misc]


@pytest.mark.parametrize("invalid_version", [0, -1, -100])
def test_agent_permissions_requires_positive_version(invalid_version: int) -> None:
    with pytest.raises(InvalidIdentityError, match="version"):
        build_permissions(version=invalid_version)


def test_agent_permissions_requires_spending_limits_type() -> None:
    with pytest.raises(InvalidIdentityError, match="limits"):
        build_permissions(limits={"per_transaction_minor": 100})


def test_enums_contain_required_specification_values() -> None:
    assert {s.value for s in TenantStatus} == {"ACTIVE", "SUSPENDED"}
    assert {r.value for r in UserRole} == {
        "ADMIN",
        "MERCHANT_ADMIN",
        "FINANCE",
        "BUYER",
        "AGENT_OPERATOR",
        "AUDITOR",
    }
    assert {s.value for s in AgentStatus} == {
        "ACTIVE",
        "SUSPENDED",
        "REVOKED",
        "EXPIRED",
        "QUARANTINED",
    }


# =============================================================================
# 2. Multi-Tenant Data Isolation Tests (§49-§51)
# =============================================================================


def test_agent_repository_cannot_get_agent_from_different_tenant() -> None:
    repo = InMemoryAgentRepository()
    agent_b = build_agent(agent_id="agent_b", tenant_id="tenant_b")
    repo.save("tenant_b", agent_b)

    # Looking up Tenant B's agent under Tenant A returns None
    result = repo.get("tenant_a", "agent_b")
    assert result is None


def test_agent_repository_saving_with_mismatched_tenant_id_raises() -> None:
    repo = InMemoryAgentRepository()
    agent_b = build_agent(agent_id="agent_b", tenant_id="tenant_b")

    # Mismatch between context tenant_id and entity tenant_id
    with pytest.raises(CrossTenantAccessError, match="tenant_id mismatch"):
        repo.save("tenant_a", agent_b)


def test_agent_repository_list_strictly_isolated_by_tenant() -> None:
    repo = InMemoryAgentRepository()
    agent_a1 = build_agent(agent_id="agent_a1", tenant_id="tenant_a")
    agent_a2 = build_agent(agent_id="agent_a2", tenant_id="tenant_a")
    agent_b1 = build_agent(agent_id="agent_b1", tenant_id="tenant_b")

    repo.save("tenant_a", agent_a1)
    repo.save("tenant_a", agent_a2)
    repo.save("tenant_b", agent_b1)

    agents_a = repo.list("tenant_a")
    agents_b = repo.list("tenant_b")

    assert {a.agent_id for a in agents_a} == {"agent_a1", "agent_a2"}
    assert {b.agent_id for b in agents_b} == {"agent_b1"}


def test_tenant_repository_get_returns_none_for_unknown_tenant() -> None:
    repo = InMemoryTenantRepository()
    assert repo.get("non_existent_tenant") is None


def test_service_authorize_action_rejects_cross_tenant_action() -> None:
    service = AgentAuthorizationService()
    agent_a = build_agent(tenant_id="tenant_a")
    action_b = build_action_request(tenant_id="tenant_b")

    with pytest.raises(CrossTenantAccessError, match="ActionRequest belongs to different tenant"):
        service.authorize_action(agent=agent_a, action=action_b, tenant_id="tenant_a")


def test_service_authorize_action_rejects_mismatched_context_tenant() -> None:
    service = AgentAuthorizationService()
    agent_a = build_agent(tenant_id="tenant_a")
    action_a = build_action_request(tenant_id="tenant_a")

    with pytest.raises(CrossTenantAccessError, match="Context tenant_id mismatch"):
        service.authorize_action(agent=agent_a, action=action_a, tenant_id="tenant_foreign")


def test_cross_tenant_error_does_not_leak_target_data() -> None:
    service = AgentAuthorizationService()
    agent_a = build_agent(tenant_id="tenant_alpha")
    action_b = build_action_request(tenant_id="tenant_beta_confidential")

    with pytest.raises(CrossTenantAccessError) as exc_info:
        service.authorize_action(agent=agent_a, action=action_b, tenant_id="tenant_alpha")

    error_msg = str(exc_info.value)
    assert "tenant_beta_confidential" not in error_msg


# =============================================================================
# 3. Agent Lifecycle State Machine Tests (§15, §2.4)
# =============================================================================


def test_active_agent_passes_active_validation() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=AgentStatus.ACTIVE)
    # Valid active agent raises no error
    service.validate_agent_active(agent)


def test_suspended_agent_fails_closed() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=AgentStatus.SUSPENDED)
    with pytest.raises(AgentSuspendedError) as exc:
        service.validate_agent_active(agent)
    assert issubclass(AgentSuspendedError, AgentNotActiveError)
    assert issubclass(AgentNotActiveError, DomainError)
    assert exc.value.code == "AGENT_SUSPENDED"


def test_revoked_agent_fails_closed() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=AgentStatus.REVOKED)
    with pytest.raises(AgentRevokedError) as exc:
        service.validate_agent_active(agent)
    assert exc.value.code == "AGENT_REVOKED"


def test_expired_status_agent_fails_closed() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=AgentStatus.EXPIRED)
    with pytest.raises(AgentExpiredError) as exc:
        service.validate_agent_active(agent)
    assert exc.value.code == "AGENT_EXPIRED"


def test_temporally_expired_agent_fails_closed() -> None:
    service = AgentAuthorizationService()
    created = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    expires = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    agent = build_agent(status=AgentStatus.ACTIVE, created_at=created, expires_at=expires)

    evaluation_time = datetime(2026, 1, 3, 0, 0, tzinfo=UTC)
    with pytest.raises(AgentExpiredError):
        service.validate_agent_active(agent, now=evaluation_time)


def test_quarantined_agent_fails_closed() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=AgentStatus.QUARANTINED)
    with pytest.raises(AgentQuarantinedError) as exc:
        service.validate_agent_active(agent)
    assert exc.value.code == "AGENT_QUARANTINED"


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        (AgentStatus.SUSPENDED, AgentSuspendedError),
        (AgentStatus.REVOKED, AgentRevokedError),
        (AgentStatus.EXPIRED, AgentExpiredError),
        (AgentStatus.QUARANTINED, AgentQuarantinedError),
    ],
)
def test_non_active_agent_fails_action_authorization(
    status: AgentStatus,
    expected_error: type[Exception],
) -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=status)
    action = build_action_request()
    with pytest.raises(expected_error):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


def test_repository_status_update_active_to_suspended_and_back() -> None:
    repo = InMemoryAgentRepository()
    agent = build_agent(status=AgentStatus.ACTIVE)
    repo.save("tenant_01", agent)

    repo.update_status("tenant_01", agent.agent_id, AgentStatus.SUSPENDED)
    updated = repo.get("tenant_01", agent.agent_id)
    assert updated is not None
    assert updated.status is AgentStatus.SUSPENDED

    repo.update_status("tenant_01", agent.agent_id, AgentStatus.ACTIVE)
    restored = repo.get("tenant_01", agent.agent_id)
    assert restored is not None
    assert restored.status is AgentStatus.ACTIVE


def test_revocation_is_permanent_terminal_state() -> None:
    repo = InMemoryAgentRepository()
    agent = build_agent(status=AgentStatus.ACTIVE)
    repo.save("tenant_01", agent)

    repo.update_status("tenant_01", agent.agent_id, AgentStatus.REVOKED)
    revoked = repo.get("tenant_01", agent.agent_id)
    assert revoked is not None
    assert revoked.status is AgentStatus.REVOKED

    # Attempting to reactivate a revoked agent is strictly rejected
    with pytest.raises(InvalidIdentityError, match="Cannot transition from terminal REVOKED"):
        repo.update_status("tenant_01", agent.agent_id, AgentStatus.ACTIVE)


def test_quarantined_agent_cannot_be_directly_reactivated() -> None:
    repo = InMemoryAgentRepository()
    agent = build_agent(status=AgentStatus.QUARANTINED)
    repo.save("tenant_01", agent)

    with pytest.raises(
        InvalidIdentityError, match="Cannot transition QUARANTINED directly to ACTIVE"
    ):
        repo.update_status("tenant_01", agent.agent_id, AgentStatus.ACTIVE)


# =============================================================================
# 4. Privilege Escalation Defense Tests (§0.4, §15, §17, §112)
# =============================================================================


def test_agent_cannot_modify_its_own_permissions() -> None:
    service = AgentAuthorizationService()
    new_perms = build_permissions(
        agent_id="agent_01",
        limits=SpendingLimits(per_transaction_minor=10_000_000),
    )

    with pytest.raises(PrivilegeEscalationAttemptError, match="Agent cannot alter permissions"):
        service.update_agent_permissions(
            tenant_id="tenant_01",
            agent_id="agent_01",
            permissions=new_perms,
            caller_principal_id="agent_01",
            caller_is_agent=True,
        )


def test_agent_cannot_modify_another_agent_permissions() -> None:
    service = AgentAuthorizationService()
    new_perms = build_permissions(agent_id="agent_02")

    with pytest.raises(PrivilegeEscalationAttemptError, match="Agent cannot alter permissions"):
        service.update_agent_permissions(
            tenant_id="tenant_01",
            agent_id="agent_02",
            permissions=new_perms,
            caller_principal_id="agent_01",
            caller_is_agent=True,
        )


def test_agent_submitting_permission_grant_action_is_strictly_rejected() -> None:
    service = AgentAuthorizationService()
    agent = build_agent()
    escalation_action = build_action_request(
        action="update_permissions",
        domain="identity",
        resource=ResourceRef(type="agent_permissions", id="agent_01"),
    )

    with pytest.raises(
        PrivilegeEscalationAttemptError, match="cannot perform administrative action"
    ):
        service.authorize_action(agent=agent, action=escalation_action, tenant_id="tenant_01")


def test_agent_submitting_limit_widening_action_is_strictly_rejected() -> None:
    service = AgentAuthorizationService()
    agent = build_agent()
    escalation_action = build_action_request(
        action="widen_spending_limits",
        domain="policy",
        resource=ResourceRef(type="limits", id="agent_01"),
    )

    with pytest.raises(
        PrivilegeEscalationAttemptError, match="cannot perform administrative action"
    ):
        service.authorize_action(agent=agent, action=escalation_action, tenant_id="tenant_01")


def test_human_admin_can_update_agent_permissions() -> None:
    service = AgentAuthorizationService()
    repo = InMemoryAgentPermissionsRepository()
    service._permissions_repo = repo
    agent = build_agent()

    new_perms = build_permissions(
        agent_id="agent_01",
        limits=SpendingLimits(per_transaction_minor=500_000),
        version=2,
    )

    # Calling as human admin succeeds
    service.update_agent_permissions(
        tenant_id="tenant_01",
        agent_id=agent.agent_id,
        permissions=new_perms,
        caller_principal_id="admin_user_01",
        caller_is_agent=False,
    )
    assert repo.get("tenant_01", agent.agent_id) == new_perms


def test_permissions_assert_no_escalation_detects_widened_capabilities() -> None:
    current = build_permissions(capabilities=("catalog.search",))
    proposed = build_permissions(capabilities=("catalog.search", "payment.request"))

    with pytest.raises(PrivilegeEscalationAttemptError, match="Cannot widen capabilities"):
        current.assert_no_escalation(proposed)


def test_permissions_assert_no_escalation_detects_increased_limits() -> None:
    current = build_permissions(
        limits=SpendingLimits(per_transaction_minor=100_000, daily_minor=200_000)
    )
    proposed_tx = build_permissions(
        limits=SpendingLimits(per_transaction_minor=150_000, daily_minor=200_000)
    )
    with pytest.raises(
        PrivilegeEscalationAttemptError, match="Cannot increase per_transaction_minor"
    ):
        current.assert_no_escalation(proposed_tx)

    proposed_daily = build_permissions(
        limits=SpendingLimits(per_transaction_minor=100_000, daily_minor=250_000)
    )
    with pytest.raises(PrivilegeEscalationAttemptError, match="Cannot increase daily_minor"):
        current.assert_no_escalation(proposed_daily)

    proposed_remove_ceiling = build_permissions(
        limits=SpendingLimits(per_transaction_minor=None, daily_minor=200_000)
    )
    with pytest.raises(
        PrivilegeEscalationAttemptError, match="Cannot remove per_transaction_minor ceiling"
    ):
        current.assert_no_escalation(proposed_remove_ceiling)


# =============================================================================
# 5. Capability Authorization Tests (§16, §17)
# =============================================================================


def test_agent_with_required_capability_is_authorized() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=("catalog.search", "payment.request"))
    action = build_action_request(action="purchase")

    assert service.authorize_action(agent=agent, action=action, tenant_id="tenant_01") is True


def test_agent_without_payment_request_fails_purchase_authorization() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=("catalog.search", "catalog.compare"))
    action = build_action_request(action="purchase")

    with pytest.raises(MissingCapabilityError) as exc:
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")
    assert exc.value.code == "MISSING_CAPABILITY"
    assert "payment.request" in str(exc.value)


def test_agent_without_catalog_search_fails_search_action() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=("cart.create",))
    action = build_action_request(action="catalog_search")

    with pytest.raises(MissingCapabilityError, match=r"catalog\.search"):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


def test_agent_with_empty_capabilities_fails_all_actions() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=())
    action = build_action_request(action="purchase")

    with pytest.raises(MissingCapabilityError):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


def test_capability_matching_is_case_sensitive_and_exact() -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=("PAYMENT.REQUEST", "payment.*"))
    action = build_action_request(action="purchase")

    # Uppercase or wildcard strings do not satisfy exact capability requirement
    with pytest.raises(MissingCapabilityError):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


def test_abac_allowed_categories_restriction() -> None:
    service = AgentAuthorizationService()
    perms = build_permissions(allowed_categories=("electronics", "books"))
    agent = build_agent()

    # Allowed category succeeds
    action_allowed = build_action_request(
        resource=ResourceRef(type="product:electronics", id="item_1")
    )
    assert service.validate_category_allowed(agent, perms, action_allowed) is True

    # Disallowed category raises DomainError
    action_disallowed = build_action_request(
        resource=ResourceRef(type="product:jewelry", id="item_2")
    )
    with pytest.raises(DomainError, match="Category 'jewelry' is not permitted"):
        service.validate_category_allowed(agent, perms, action_disallowed)


# =============================================================================
# 6. Cache Key Generation Helper Tests (§51)
# =============================================================================


def test_format_cache_key_standard_format() -> None:
    assert (
        format_cache_key("tenant_01", "product", "prod_123") == "tenant:tenant_01:product:prod_123"
    )
    assert (
        format_tenant_cache_key("tenant_01", "product", "prod_123")
        == "tenant:tenant_01:product:prod_123"
    )


@pytest.mark.parametrize(
    ("resource_type", "resource_id", "expected"),
    [
        ("agent", "ag_456", "tenant:tenant_01:agent:ag_456"),
        ("permissions", "ag_456", "tenant:tenant_01:permissions:ag_456"),
        ("policy", "pol_789", "tenant:tenant_01:policy:pol_789"),
        ("order", "ord_001", "tenant:tenant_01:order:ord_001"),
    ],
)
def test_format_cache_key_for_core_resources(
    resource_type: str,
    resource_id: str,
    expected: str,
) -> None:
    assert format_cache_key("tenant_01", resource_type, resource_id) == expected


@pytest.mark.parametrize("empty_part", ["", "   ", "\t"])
def test_format_cache_key_rejects_empty_components(empty_part: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        format_cache_key(empty_part, "product", "123")
    with pytest.raises(ValueError, match="cannot be empty"):
        format_cache_key("tenant_01", empty_part, "123")
    with pytest.raises(ValueError, match="cannot be empty"):
        format_cache_key("tenant_01", "product", empty_part)


def test_format_cache_key_prevents_delimiter_collision() -> None:
    # Delimiter injection prevention: colons inside components are rejected to prevent namespace spoofing
    with pytest.raises(ValueError, match="Colons are not permitted in cache key segments"):
        format_cache_key("tenant:injected", "product", "123")
    with pytest.raises(ValueError, match="Colons are not permitted in cache key segments"):
        format_cache_key("tenant_01", "product:injected", "123")


# =============================================================================
# 7. Concurrency & Architecture Purity Tests
# =============================================================================


def test_in_memory_repository_concurrent_access() -> None:
    """10 concurrent threads inserting and reading agents across distinct tenants."""
    repo = InMemoryAgentRepository()

    def worker(worker_id: int) -> bool:
        t_id = f"tenant_{worker_id}"
        a_id = f"agent_{worker_id}"
        agent = build_agent(agent_id=a_id, tenant_id=t_id)
        repo.save(t_id, agent)
        retrieved = repo.get(t_id, a_id)
        return retrieved is not None and retrieved.agent_id == a_id

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker, range(10)))

    assert all(results)


def test_identity_domain_has_zero_external_dependencies() -> None:
    """Verifies that agentpay.domain.identity imports only from standard library and internal domain."""
    import agentpay.domain.identity as id_mod

    forbidden = {"fastapi", "sqlalchemy", "redis", "pydantic", "httpx", "requests"}
    for attr in dir(id_mod):
        val = getattr(id_mod, attr)
        if hasattr(val, "__module__") and val.__module__:
            root_pkg = val.__module__.split(".")[0]
            assert root_pkg not in forbidden, f"Forbidden import found: {root_pkg}"


# =============================================================================
# 8. Property-Based Tests (Hypothesis)
# =============================================================================

SAFE_IDENTIFIERS = st.from_regex(r"^[a-zA-Z0-9_\-]{1,32}$", fullmatch=True)


@given(tenant_a=SAFE_IDENTIFIERS, tenant_b=SAFE_IDENTIFIERS, agent_id=SAFE_IDENTIFIERS)
def test_property_tenant_isolation_is_strictly_invariant(
    tenant_a: str,
    tenant_b: str,
    agent_id: str,
) -> None:
    if tenant_a == tenant_b:
        return
    repo = InMemoryAgentRepository()
    agent_b = build_agent(agent_id=agent_id, tenant_id=tenant_b)
    repo.save(tenant_b, agent_b)

    assert repo.get(tenant_a, agent_id) is None


@given(status=st.sampled_from([s for s in AgentStatus if s != AgentStatus.ACTIVE]))
def test_property_all_non_active_statuses_fail_closed(status: AgentStatus) -> None:
    service = AgentAuthorizationService()
    agent = build_agent(status=status)
    action = build_action_request()

    with pytest.raises(AgentNotActiveError):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


@given(
    caps=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz.", min_size=1, max_size=20).filter(
            lambda c: c != "payment.request"
        ),
        max_size=10,
    )
)
def test_property_missing_payment_capability_always_denies_purchase(caps: list[str]) -> None:
    service = AgentAuthorizationService()
    agent = build_agent(capabilities=tuple(caps))
    action = build_action_request(action="purchase")

    with pytest.raises(MissingCapabilityError):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


@given(
    tenant_id=SAFE_IDENTIFIERS,
    resource_type=SAFE_IDENTIFIERS,
    resource_id=SAFE_IDENTIFIERS,
)
def test_property_cache_key_is_deterministic_and_well_formed(
    tenant_id: str,
    resource_type: str,
    resource_id: str,
) -> None:
    key = format_cache_key(tenant_id, resource_type, resource_id)
    parts = key.split(":")
    assert len(parts) == 4
    assert parts[0] == "tenant"
    assert parts[1] == tenant_id
    assert parts[2] == resource_type
    assert parts[3] == resource_id


# =============================================================================
# 9. Milestone 1 Iteration 2 Hardening & Adversarial Regression Tests (§0.4, §15-§17, §49-§51)
# =============================================================================


def test_service_authorize_action_rejects_spoofed_principal_id() -> None:
    """Agent bound to principal_id='user_operator' cannot forge principal_id='user_admin' in ActionRequest (§6, §15)."""
    service = AgentAuthorizationService()
    agent = build_agent(agent_id="agent_01", principal_id="user_operator", tenant_id="tenant_01")
    action_spoofed = build_action_request(
        agent_id="agent_01",
        principal_id="user_admin",
        tenant_id="tenant_01",
    )

    with pytest.raises(
        InvalidIdentityError, match="Action principal_id 'user_admin' does not match"
    ):
        service.authorize_action(agent=agent, action=action_spoofed, tenant_id="tenant_01")


def test_service_authorize_action_succeeds_when_principal_id_matches() -> None:
    """ActionRequest with matching principal_id is authorized successfully."""
    service = AgentAuthorizationService()
    agent = build_agent(agent_id="agent_01", principal_id="user_operator", tenant_id="tenant_01")
    action_legit = build_action_request(
        agent_id="agent_01",
        principal_id="user_operator",
        tenant_id="tenant_01",
    )

    assert service.authorize_action(agent=agent, action=action_legit, tenant_id="tenant_01") is True


@pytest.mark.parametrize("invalid_tenant", [None, "", "   ", "\t"])
def test_agent_permissions_requires_non_empty_tenant_id(invalid_tenant: object) -> None:
    """AgentPermissions strictly requires a non-empty tenant_id string (§0.4, §49, §50)."""
    with pytest.raises(InvalidIdentityError, match="tenant_id"):
        build_permissions(tenant_id=invalid_tenant)


def test_service_authorize_action_rejects_mismatched_permissions_tenant_id() -> None:
    """External permissions belonging to another tenant are rejected with CrossTenantAccessError."""
    service = AgentAuthorizationService()
    agent = build_agent(tenant_id="tenant_01")
    action = build_action_request(tenant_id="tenant_01")
    foreign_perms = build_permissions(tenant_id="tenant_02")

    with pytest.raises(
        CrossTenantAccessError, match="Permissions tenant does not match execution context"
    ):
        service.authorize_action(
            agent=agent,
            action=action,
            tenant_id="tenant_01",
            permissions=foreign_perms,
        )


@pytest.mark.parametrize(
    "bad_action",
    [
        "UPDATE_PERMISSIONS",
        "Update_Permissions",
        "widen_spending_limits ",
        "  grant_capability  ",
        "ELEVATE_ROLE",
        "Modify_Policy  ",
        "  update_limits",
        "REVOKE_LIMIT\t",
        "Register_Agent",
    ],
)
def test_agent_submitting_cased_or_padded_forbidden_action_is_strictly_rejected(
    bad_action: str,
) -> None:
    """Evasion attempts on FORBIDDEN_AGENT_ACTIONS using casing or whitespace fail closed (§0.4, §15, §112)."""
    service = AgentAuthorizationService()
    agent = build_agent(tenant_id="tenant_01")
    action = build_action_request(action=bad_action, tenant_id="tenant_01")

    with pytest.raises(
        PrivilegeEscalationAttemptError, match="cannot perform administrative action"
    ):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


def test_permissions_assert_no_escalation_detects_increased_approval_threshold() -> None:
    """Prevent widening requires_approval_above_minor threshold (§0.4, §2.2)."""
    cur = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=100,
        ),
        requires_approval_above_minor=100,
    )
    prop = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=900,
        ),
        requires_approval_above_minor=900,
    )
    with pytest.raises(PrivilegeEscalationAttemptError, match="Cannot increase approval threshold"):
        cur.assert_no_escalation(prop)

    # Escalation in limits object alone is also rejected
    cur_lim = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=100,
        ),
        requires_approval_above_minor=None,
    )
    prop_lim = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=500,
        ),
        requires_approval_above_minor=None,
    )
    with pytest.raises(
        PrivilegeEscalationAttemptError,
        match=r"Cannot increase.*approval threshold",
    ):
        cur_lim.assert_no_escalation(prop_lim)


def test_permissions_assert_no_escalation_detects_removed_approval_threshold() -> None:
    """Prevent removing requires_approval_above_minor threshold ceiling (§0.4, §2.2)."""
    cur = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=100,
        ),
        requires_approval_above_minor=100,
    )
    prop = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=None,
        ),
        requires_approval_above_minor=None,
    )
    with pytest.raises(PrivilegeEscalationAttemptError, match="Cannot remove approval threshold"):
        cur.assert_no_escalation(prop)

    # Removal in limits object alone is also rejected
    cur_lim = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=100,
        ),
        requires_approval_above_minor=None,
    )
    prop_lim = build_permissions(
        limits=SpendingLimits(
            per_transaction_minor=1000,
            daily_minor=2000,
            requires_approval_above_minor=None,
        ),
        requires_approval_above_minor=None,
    )
    with pytest.raises(
        PrivilegeEscalationAttemptError,
        match=r"Cannot remove.*approval threshold",
    ):
        cur_lim.assert_no_escalation(prop_lim)


def test_permissions_assert_no_escalation_detects_widened_categories() -> None:
    """Adding new allowed_categories widens purchasing capability and is blocked (§0.4, §17)."""
    cur = build_permissions(allowed_categories=("books",))
    prop = build_permissions(allowed_categories=("books", "weapons"))

    with pytest.raises(PrivilegeEscalationAttemptError, match="Cannot widen allowed_categories"):
        cur.assert_no_escalation(prop)


def test_permissions_assert_no_escalation_detects_cleared_categories() -> None:
    """Clearing allowed_categories to () removes all restrictions and is blocked (§0.4, §17)."""
    cur = build_permissions(allowed_categories=("books",))
    prop = build_permissions(allowed_categories=())

    with pytest.raises(
        PrivilegeEscalationAttemptError, match="Cannot remove allowed_categories restriction"
    ):
        cur.assert_no_escalation(prop)


def test_permissions_assert_no_escalation_allows_narrowed_categories() -> None:
    """Narrowing allowed_categories reduces privileges and is permitted (§0.4, §17)."""
    cur = build_permissions(allowed_categories=("books", "electronics", "groceries"))
    prop = build_permissions(allowed_categories=("books", "electronics"))
    # Should not raise
    cur.assert_no_escalation(prop)


def test_authorize_action_queries_permissions_repo_when_permissions_none() -> None:
    """When permissions=None, authorize_action queries permissions_repo to reflect runtime revocations."""
    repo = InMemoryAgentPermissionsRepository()
    agent = build_agent(capabilities=("catalog.search", "payment.request"))
    # Stored permissions only grant catalog.search (payment.request was revoked by operator)
    stored = build_permissions(
        agent_id=agent.agent_id,
        tenant_id=agent.tenant_id,
        capabilities=("catalog.search",),
    )
    repo.save("tenant_01", stored)

    service = AgentAuthorizationService(permissions_repo=repo)
    action = build_action_request(action="purchase")

    with pytest.raises(MissingCapabilityError, match=r"payment\.request"):
        service.authorize_action(
            agent=agent, action=action, tenant_id="tenant_01", permissions=None
        )


def test_authorize_action_rejects_external_permissions_escalating_capabilities() -> None:
    """Passing external permissions parameter with capabilities beyond baseline raises PrivilegeEscalationAttemptError."""
    agent = build_agent(capabilities=("catalog.search",))
    escalated_perms = build_permissions(
        agent_id=agent.agent_id,
        tenant_id=agent.tenant_id,
        capabilities=("catalog.search", "payment.request"),
    )
    service = AgentAuthorizationService()
    action = build_action_request(action="purchase")

    with pytest.raises(
        PrivilegeEscalationAttemptError,
        match=r"[Cc]annot widen capabilities",
    ):
        service.authorize_action(
            agent=agent,
            action=action,
            tenant_id="tenant_01",
            permissions=escalated_perms,
        )

    # When permissions_repo is configured with revoked capabilities, external escalation is also blocked
    repo = InMemoryAgentPermissionsRepository()
    repo.save(
        "tenant_01",
        build_permissions(
            agent_id=agent.agent_id,
            tenant_id=agent.tenant_id,
            capabilities=("catalog.search",),
        ),
    )
    service_with_repo = AgentAuthorizationService(permissions_repo=repo)
    with pytest.raises(
        PrivilegeEscalationAttemptError,
        match=r"[Cc]annot widen capabilities",
    ):
        service_with_repo.authorize_action(
            agent=agent,
            action=action,
            tenant_id="tenant_01",
            permissions=escalated_perms,
        )


@pytest.mark.parametrize("bad_segment", ["tenant\r\n01", "t1\x00extra", "t1\n02", "t1\r03"])
def test_format_cache_key_rejects_control_characters(bad_segment: str) -> None:
    """format_tenant_cache_key rejects CRLF and null-byte injection vectors (§51)."""
    with pytest.raises(ValueError, match="Control characters"):
        format_tenant_cache_key(bad_segment, "agent", "01")
    with pytest.raises(ValueError, match="Control characters"):
        format_tenant_cache_key("tenant_01", bad_segment, "01")
    with pytest.raises(ValueError, match="Control characters"):
        format_tenant_cache_key("tenant_01", "agent", bad_segment)


def test_repository_save_cannot_overwrite_or_resurrect_revoked_agent() -> None:
    """InMemoryAgentRepository.save() cannot overwrite a terminal REVOKED agent with an ACTIVE one (§15)."""
    repo = InMemoryAgentRepository()
    revoked = build_agent(agent_id="agent_rev", status=AgentStatus.REVOKED)
    repo.save("tenant_01", revoked)
    saved = repo.get("tenant_01", "agent_rev")
    assert saved is not None
    assert saved.status is AgentStatus.REVOKED

    active_replacement = build_agent(agent_id="agent_rev", status=AgentStatus.ACTIVE)
    with pytest.raises(
        AgentRevokedError, match="Cannot overwrite or resurrect terminal REVOKED agent"
    ):
        repo.save("tenant_01", active_replacement)

    # Confirm status was not changed in memory
    final_agent = repo.get("tenant_01", "agent_rev")
    assert final_agent is not None
    assert final_agent.status is AgentStatus.REVOKED


def test_adversarial_suite_all_29_scenarios() -> None:
    """Comprehensive test harness executing all 29 adversarial scenarios from challenger_m1_1 and challenger_m1_2."""
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    service = AgentAuthorizationService()
    repo_agent = InMemoryAgentRepository()
    repo_perms = InMemoryAgentPermissionsRepository()

    # 1.1 Cross-tenant ActionRequest
    agent = build_agent(tenant_id="t1")
    act_cross = build_action_request(tenant_id="t2")
    with pytest.raises(CrossTenantAccessError):
        service.authorize_action(agent, act_cross, "t1")

    # 1.2 Cross-tenant Agent context
    with pytest.raises(CrossTenantAccessError):
        service.authorize_action(agent, build_action_request(tenant_id="t1"), "foreign_tenant")

    # 1.3 Repo cross-tenant save
    with pytest.raises(CrossTenantAccessError):
        repo_agent.save("t2", agent)

    # 1.4 Repo partition leakage
    repo_agent.save("t1", agent)
    assert repo_agent.get("t2", agent.agent_id) is None

    # 1.5 Foreign tenant permissions
    foreign_p = build_permissions(tenant_id="t2", agent_id=agent.agent_id)
    with pytest.raises(CrossTenantAccessError):
        service.authorize_action(
            agent, build_action_request(tenant_id="t1"), "t1", permissions=foreign_p
        )

    # 1.6 Unbound permissions cross-tenant bypass
    with pytest.raises(InvalidIdentityError):
        AgentPermissions(agent.agent_id, None, ("payment.request",), SpendingLimits(100, 100))  # type: ignore[arg-type]

    # 2.1 Action agent_id spoofing
    act_bad_agent = build_action_request(agent_id="spoofed_agent", tenant_id="t1")
    with pytest.raises(CrossTenantAccessError):
        service.authorize_action(agent, act_bad_agent, "t1")

    # 2.2 Permissions agent_id mismatch
    p_bad_agent = build_permissions(tenant_id="t1", agent_id="different_agent")
    with pytest.raises(InvalidIdentityError):
        service.authorize_action(
            agent, build_action_request(tenant_id="t1"), "t1", permissions=p_bad_agent
        )

    # 2.3 Principal ID spoofing in ActionRequest
    act_bad_principal = build_action_request(
        principal_id="spoofed_admin", agent_id=agent.agent_id, tenant_id="t1"
    )
    with pytest.raises(InvalidIdentityError):
        service.authorize_action(agent, act_bad_principal, "t1")

    # 3.1 Forbidden administrative actions
    for forbidden in FORBIDDEN_AGENT_ACTIONS:
        act_forbid = build_action_request(
            action=forbidden,
            agent_id=agent.agent_id,
            principal_id=agent.principal_id,
            tenant_id="t1",
        )
        with pytest.raises(PrivilegeEscalationAttemptError):
            service.authorize_action(agent, act_forbid, "t1")

    # 3.2 Case/whitespace variation on forbidden actions
    for var in ["UPDATE_PERMISSIONS", "Update_Permissions", "widen_spending_limits "]:
        act_var = build_action_request(
            action=var, agent_id=agent.agent_id, principal_id=agent.principal_id, tenant_id="t1"
        )
        with pytest.raises(PrivilegeEscalationAttemptError):
            service.authorize_action(agent, act_var, "t1")

    # 3.3 Agent self-permission update
    with pytest.raises(PrivilegeEscalationAttemptError):
        service.update_agent_permissions(
            "t1",
            agent.agent_id,
            build_permissions(tenant_id="t1", agent_id=agent.agent_id),
            caller_principal_id="p1",
            caller_is_agent=True,
        )

    # 3.4 Approval threshold escalation
    cur_p = build_permissions(tenant_id="t1", requires_approval_above_minor=100)
    prop_p = build_permissions(tenant_id="t1", requires_approval_above_minor=900)
    with pytest.raises(PrivilegeEscalationAttemptError):
        cur_p.assert_no_escalation(prop_p)

    # 3.5 Category restriction widening
    cur_cat = build_permissions(tenant_id="t1", allowed_categories=("books",))
    prop_cat = build_permissions(tenant_id="t1", allowed_categories=("books", "weapons"))
    with pytest.raises(PrivilegeEscalationAttemptError):
        cur_cat.assert_no_escalation(prop_cat)

    # 3.6 Category restriction removal
    prop_cat_empty = build_permissions(tenant_id="t1", allowed_categories=())
    with pytest.raises(PrivilegeEscalationAttemptError):
        cur_cat.assert_no_escalation(prop_cat_empty)

    # 3.7 Unverified permissions parameter escalation
    restricted_agent = build_agent(
        agent_id="a_rest",
        principal_id="p_rest",
        tenant_id="t1",
        capabilities=("catalog.search",),
    )
    escalated_p = build_permissions(
        agent_id="a_rest",
        tenant_id="t1",
        capabilities=("catalog.search", "payment.request"),
    )
    with pytest.raises(PrivilegeEscalationAttemptError):
        service.authorize_action(
            restricted_agent,
            build_action_request(
                agent_id="a_rest", principal_id="p_rest", tenant_id="t1", action="purchase"
            ),
            "t1",
            permissions=escalated_p,
        )

    # 3.8 Permissions repository consultation disconnect
    repo_perms.save(
        "t1",
        build_permissions(
            agent_id="a_sync",
            tenant_id="t1",
            capabilities=("catalog.search",),
        ),
    )
    agent_sync = build_agent(
        agent_id="a_sync",
        principal_id="p_sync",
        tenant_id="t1",
        capabilities=("catalog.search", "payment.request"),
    )
    svc_sync = AgentAuthorizationService(permissions_repo=repo_perms)
    with pytest.raises(MissingCapabilityError):
        svc_sync.authorize_action(
            agent_sync,
            build_action_request(
                agent_id="a_sync", principal_id="p_sync", tenant_id="t1", action="purchase"
            ),
            "t1",
            permissions=None,
        )

    # 4.1 Revoked agent execution
    revoked = build_agent(
        agent_id="a_rev", principal_id="p_rev", tenant_id="t1", status=AgentStatus.REVOKED
    )
    with pytest.raises(AgentRevokedError):
        service.authorize_action(
            revoked,
            build_action_request(agent_id="a_rev", principal_id="p_rev", tenant_id="t1"),
            "t1",
        )

    # 4.2 Revoked agent resurrection (entity)
    with pytest.raises(AgentRevokedError):
        revoked.transition_to(AgentStatus.ACTIVE)

    # 4.3 Revoked agent resurrection (repo update_status)
    repo_agent.save("t1", revoked)
    with pytest.raises(AgentRevokedError):
        repo_agent.update_status("t1", "a_rev", AgentStatus.ACTIVE)

    # 4.4 Quarantined direct to ACTIVE
    quarantined = build_agent(agent_id="a_quar", tenant_id="t1", status=AgentStatus.QUARANTINED)
    with pytest.raises(InvalidIdentityError):
        quarantined.transition_to(AgentStatus.ACTIVE)

    # 4.5 Expired agent execution
    expired = build_agent(
        agent_id="a_exp",
        principal_id="p_exp",
        tenant_id="t1",
        expires_at=now - timedelta(days=1),
        created_at=now - timedelta(days=10),
    )
    with pytest.raises(AgentExpiredError):
        service.authorize_action(
            expired,
            build_action_request(agent_id="a_exp", principal_id="p_exp", tenant_id="t1"),
            "t1",
            now=now,
        )

    # 4.6 Suspended agent execution
    suspended = build_agent(
        agent_id="a_susp", principal_id="p_susp", tenant_id="t1", status=AgentStatus.SUSPENDED
    )
    with pytest.raises(AgentSuspendedError):
        service.authorize_action(
            suspended,
            build_action_request(agent_id="a_susp", principal_id="p_susp", tenant_id="t1"),
            "t1",
        )

    # 4.7 Future-dated agent execution
    future_agent = build_agent(
        agent_id="a_fut",
        principal_id="p_fut",
        tenant_id="t1",
        created_at=now + timedelta(days=5),
        expires_at=now + timedelta(days=10),
    )
    with pytest.raises(AgentNotActiveError):
        service.authorize_action(
            future_agent,
            build_action_request(agent_id="a_fut", principal_id="p_fut", tenant_id="t1"),
            "t1",
            now=now,
        )

    # 5.1 Cache key colon injection
    with pytest.raises(ValueError):
        format_tenant_cache_key("tenant:01", "agent", "01")

    # 5.2 Cache key CRLF injection
    with pytest.raises(ValueError):
        format_tenant_cache_key("t1\r\nSET injected", "agent", "01")

    # 5.3 Cache key null-byte injection
    with pytest.raises(ValueError):
        format_tenant_cache_key("t1\x00extra", "agent", "01")

    # 6.1 Agent entity immutability
    with pytest.raises(FrozenInstanceError):
        agent.status = AgentStatus.REVOKED  # type: ignore[misc]

    # 6.2 Repository concurrency (100 threads)
    repo_c = InMemoryAgentRepository()

    def conc_task(tid: int) -> bool:
        t_name = f"tenant_{tid % 5}"
        a_name = f"agent_{tid}"
        ag = build_agent(agent_id=a_name, tenant_id=t_name)
        repo_c.save(t_name, ag)
        ret = repo_c.get(t_name, a_name)
        return ret is not None and ret.agent_id == a_name

    with ThreadPoolExecutor(max_workers=20) as executor:
        conc_results = list(executor.map(conc_task, range(100)))
    assert all(conc_results)

    # Bonus: Overwrite resurrection prevention
    active_clone = build_agent(
        agent_id="a_rev", principal_id="p_rev", tenant_id="t1", status=AgentStatus.ACTIVE
    )
    with pytest.raises(AgentRevokedError):
        repo_agent.save("t1", active_clone)


@given(
    bad_action=st.sampled_from(list(FORBIDDEN_AGENT_ACTIONS)),
    prefix_spaces=st.integers(min_value=0, max_value=5),
    suffix_spaces=st.integers(min_value=0, max_value=5),
    casing=st.sampled_from(["upper", "title", "lower", "mixed"]),
)
def test_property_forbidden_actions_with_arbitrary_casing_and_padding_always_fail(
    bad_action: str,
    prefix_spaces: int,
    suffix_spaces: int,
    casing: str,
) -> None:
    """Hypothesis property: any permutation of casing or whitespace on a forbidden action fails closed."""
    if casing == "upper":
        transformed = bad_action.upper()
    elif casing == "title":
        transformed = bad_action.title()
    elif casing == "mixed":
        transformed = "".join(
            c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(bad_action)
        )
    else:
        transformed = bad_action.lower()

    action_str = (" " * prefix_spaces) + transformed + (" " * suffix_spaces)

    service = AgentAuthorizationService()
    agent = build_agent(tenant_id="tenant_01")
    action = build_action_request(
        action=action_str,
        agent_id=agent.agent_id,
        principal_id=agent.principal_id,
        tenant_id="tenant_01",
    )

    with pytest.raises(PrivilegeEscalationAttemptError):
        service.authorize_action(agent=agent, action=action, tenant_id="tenant_01")


@given(
    clean_segment=SAFE_IDENTIFIERS,
    ctrl_char=st.sampled_from(["\r", "\n", "\0", "\r\n"]),
)
def test_property_cache_key_with_control_characters_always_fails(
    clean_segment: str,
    ctrl_char: str,
) -> None:
    """Hypothesis property: injecting control characters into any cache key segment always raises ValueError."""
    corrupted = f"{clean_segment}{ctrl_char}"
    with pytest.raises(ValueError):
        format_tenant_cache_key(corrupted, "agent", "01")
    with pytest.raises(ValueError):
        format_tenant_cache_key("tenant_01", corrupted, "01")
    with pytest.raises(ValueError):
        format_tenant_cache_key("tenant_01", "agent", corrupted)

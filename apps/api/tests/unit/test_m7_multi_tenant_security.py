"""Comprehensive automated test suite for Milestone M7: Multi-Tenant Isolation & Authorization Hardening.

Covers all 5 features of Milestone M7:
- Feature 26: Emergency Controls Tenant Isolation (strict scope enforcement on pause and resume)
- Feature 27: Emergency Operator Identity Spoofing Fix (rejection of operator_id mismatch)
- Feature 28: Actor Identity Verification in API (agent-to-principal delegation binding)
- Feature 29: TokenBudgetTracker Multi-Tenant Key Isolation (elimination of cross-tenant key matching)
- Feature 30: Approval Repository Multi-Tenant Scoping (mandatory tenant_id on list_pending & scoped lookups)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_approval import make_action_request
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.model_gateway.model_gateway import BudgetLimits, TokenBudgetTracker
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.application.services.approval_service import ApprovalService
from agentpay.domain.approval import Approval, ApprovalBinding, ApprovalStatus
from agentpay.domain.identity import (
    UserRole,
)
from agentpay.domain.money import Money
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)


def _action_payload(
    action_id: str,
    agent_id: str = "agent_01",
    principal_id: str = "user_01",
) -> dict[str, object]:
    return {
        "action_id": action_id,
        "principal_id": principal_id,
        "agent_id": agent_id,
        "domain": "commerce",
        "action": "purchase",
        "resource_type": "product",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 129900,
        "currency": "INR",
        "intent_purpose": "m7 multi-tenant test",
        "quantity": 1,
    }


# ===========================================================================
# Feature 26: Emergency Controls Tenant Isolation
# ===========================================================================
class TestFeature26EmergencyControlsTenantIsolation:
    """Verifies strict tenant isolation on emergency pause and resume routes (§21, §49, §67)."""

    def test_tenant_admin_cannot_pause_all(self) -> None:
        """A tenant admin passing scope='ALL' must be rejected with 403 CROSS_TENANT_ACCESS_DENIED."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        status, body = client.post(
            "/api/v1/emergency/pause",
            json_body={
                "operator_id": "admin_01",
                "scope": "ALL",
                "reason": "Unauthorized cluster-wide pause",
            },
        )
        assert status == 403
        assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"
        assert "cannot pause cluster-wide scope 'ALL'" in body["error"]["message"]
        assert app.is_emergency_paused is False

    def test_tenant_admin_cannot_pause_foreign_tenant(self) -> None:
        """A tenant admin for tenant_01 attempting to pause tenant_02 must receive 403 CROSS_TENANT_ACCESS_DENIED."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        for scope in ("tenant_02", "tenant:tenant_02"):
            status, body = client.post(
                "/api/v1/emergency/pause",
                json_body={
                    "operator_id": "admin_01",
                    "scope": scope,
                    "reason": "Rival sabotage attempt",
                },
            )
            assert status == 403
            assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"
            assert "cannot pause foreign tenant 'tenant_02'" in body["error"]["message"]
            assert app.is_emergency_paused_for("tenant_02") is False

    def test_tenant_admin_can_pause_and_resume_own_tenant(self) -> None:
        """A tenant admin can pause and resume their own tenant partition."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        # Pause own tenant
        status, body = client.post(
            "/api/v1/emergency/pause",
            json_body={
                "operator_id": "admin_01",
                "scope": "tenant_01",
                "reason": "Incident triage",
            },
        )
        assert status == 200
        assert body["status"] == "PAUSED"
        assert app.is_emergency_paused_for("tenant_01") is True
        assert app.is_emergency_paused_for("tenant_02") is False

        # Resume own tenant
        status_res, body_res = client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "admin_01", "scope": "tenant_01"},
        )
        assert status_res == 200
        assert body_res["status"] == "ACTIVE"
        assert app.is_emergency_paused_for("tenant_01") is False

    def test_tenant_admin_pause_omitted_scope_defaults_to_own_tenant(self) -> None:
        """When scope is omitted, a tenant admin's pause automatically defaults to their own tenant."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        status, body = client.post(
            "/api/v1/emergency/pause",
            json_body={"operator_id": "admin_01", "reason": "Default scope pause"},
        )
        assert status == 200
        assert "tenant:tenant_01" in body["scopes"]
        assert app.is_emergency_paused_for("tenant_01") is True
        assert app.is_emergency_paused_for("tenant_02") is False

        # Resume with omitted scope also resumes own tenant
        status_res, body_res = client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "admin_01"},
        )
        assert status_res == 200
        assert body_res["status"] == "ACTIVE"
        assert app.is_emergency_paused_for("tenant_01") is False

    def test_tenant_admin_cannot_resume_all_or_foreign_tenant(self) -> None:
        """A tenant admin cannot resume ALL or a foreign tenant partition."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        app.pause_emergency(operator_id="sec_ops", reason="Platform maintenance", scope="ALL")

        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        # Resume ALL rejected
        status, body = client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "admin_01", "scope": "ALL"},
        )
        assert status == 403
        assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"

        # Resume foreign tenant rejected
        status_f, body_f = client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "admin_01", "scope": "tenant_02"},
        )
        assert status_f == 403
        assert body_f["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"

    def test_platform_system_authority_can_pause_and_resume_all(self) -> None:
        """A credential with system authority (SYSTEM tenant or platform:admin) can pause and resume ALL."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        sys_client = ApiTestClient(app).as_system_admin(principal_id="sys_admin")

        status, body = sys_client.post(
            "/api/v1/emergency/pause",
            json_body={
                "operator_id": "sys_admin",
                "scope": "ALL",
                "reason": "Cluster-wide threat containment",
            },
        )
        assert status == 200
        assert body["status"] == "PAUSED"
        assert app.is_emergency_paused_for("tenant_01") is True
        assert app.is_emergency_paused_for("tenant_02") is True

        status_res, body_res = sys_client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "sys_admin", "scope": "ALL"},
        )
        assert status_res == 200
        assert body_res["status"] == "ACTIVE"
        assert app.is_emergency_paused is False


# ===========================================================================
# Feature 27: Emergency Operator Identity Spoofing Fix
# ===========================================================================
class TestFeature27EmergencyOperatorIdentitySpoofingFix:
    """Verifies rejection of operator identity spoofing in emergency pause and resume (§14, §21, §67)."""

    def test_operator_id_mismatch_rejected_on_pause(self) -> None:
        """When operator_id in request body mismatches authenticated cred.principal_id, reject with 403."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="actual_admin")

        status, body = client.post(
            "/api/v1/emergency/pause",
            json_body={"operator_id": "spoofed_operator", "reason": "Impersonation probe"},
        )
        assert status == 403
        assert body["error"]["code"] == "OPERATOR_IMPERSONATION_DENIED"
        assert body["error"]["details"]["authenticated_principal"] == "actual_admin"
        assert body["error"]["details"]["asserted_operator"] == "spoofed_operator"
        assert app.is_emergency_paused is False

    def test_operator_id_mismatch_rejected_on_resume(self) -> None:
        """When operator_id in request body mismatches authenticated cred.principal_id on resume, reject with 403."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="actual_admin")

        status, body = client.post(
            "/api/v1/emergency/resume",
            json_body={"operator_id": "spoofed_operator"},
        )
        assert status == 403
        assert body["error"]["code"] == "OPERATOR_IMPERSONATION_DENIED"
        assert body["error"]["details"]["authenticated_principal"] == "actual_admin"
        assert body["error"]["details"]["asserted_operator"] == "spoofed_operator"

    def test_operator_id_omitted_defaults_to_cred_principal(self) -> None:
        """When operator_id is omitted, it defaults safely to cred.principal_id."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="legit_admin")

        status, body = client.post(
            "/api/v1/emergency/pause",
            json_body={"reason": "Omitted operator test"},
        )
        assert status == 200
        assert body["metadata"]["operator_id"] == "legit_admin"


# ===========================================================================
# Feature 28: Actor Identity Verification in API
# ===========================================================================
class TestFeature28ActorIdentityVerification:
    """Verifies actor identity and delegation binding in _check_actor_identity (§14, §15, §16, §20)."""

    def test_agent_cannot_assert_unbound_principal(self) -> None:
        """An authenticated agent bound to user_01 asserting principal_id='user_02' must be rejected with 403."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        agent_client = ApiTestClient(app).as_agent("agent_01", tenant_id="tenant_01")

        # agent_01 is registered in setup_orchestrator with principal_id="user_01"
        status, body = agent_client.post(
            "/api/v1/actions",
            json_body=_action_payload(
                "act_spoof_princ", agent_id="agent_01", principal_id="user_02"
            ),
            headers={"x-tenant-id": "tenant_01"},
        )
        assert status == 403
        assert body["error"]["code"] == "PRINCIPAL_IMPERSONATION_DENIED"
        assert body["error"]["details"]["authenticated_agent"] == "agent_01"
        assert body["error"]["details"]["asserted_principal"] == "user_02"
        assert body["error"]["details"]["agent_principal"] == "user_01"

    def test_agent_asserting_bound_principal_succeeds(self) -> None:
        """An authenticated agent asserting its bound principal succeeds."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        agent_client = ApiTestClient(app).as_agent("agent_01", tenant_id="tenant_01")

        status, body = agent_client.post(
            "/api/v1/actions",
            json_body=_action_payload(
                "act_legit_princ", agent_id="agent_01", principal_id="user_01"
            ),
            headers={"x-tenant-id": "tenant_01"},
        )
        assert status == 200
        assert body["status"] == "COMPLETED"

    def test_operator_acting_on_behalf_with_mismatched_principal_rejected(self) -> None:
        """An operator asserting both agent_id and mismatched principal_id is rejected with 403."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        admin_client = ApiTestClient(app).as_admin(tenant_id="tenant_01", principal_id="admin_01")

        # agent_01 belongs to user_01, not user_99
        status, body = admin_client.post(
            "/api/v1/actions",
            json_body=_action_payload(
                "act_admin_mismatch", agent_id="agent_01", principal_id="user_99"
            ),
            headers={"x-tenant-id": "tenant_01"},
        )
        assert status == 403
        assert body["error"]["code"] == "PRINCIPAL_IMPERSONATION_DENIED"
        assert body["error"]["details"]["agent_principal"] == "user_01"
        assert body["error"]["details"]["asserted_principal"] == "user_99"

    def test_cross_tenant_credential_rejected_in_actor_identity(self) -> None:
        """A credential from tenant_01 attempting action in tenant_02 is rejected with 403 CROSS_TENANT_ACCESS_DENIED."""
        orch, *_ = setup_orchestrator()
        app = AgentPayApiApp(orchestrator=orch)
        client = ApiTestClient(app)
        token_t1 = client.create_token(
            tenant_id="tenant_01", principal_id="user_01", role=UserRole.BUYER
        )

        status, body = client.post(
            "/api/v1/actions",
            json_body=_action_payload("act_cross_t"),
            headers={"x-tenant-id": "tenant_02", "authorization": f"Bearer {token_t1}"},
        )
        assert status == 403
        assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"


# ===========================================================================
# Feature 29: TokenBudgetTracker Multi-Tenant Key Isolation
# ===========================================================================
class TestFeature29TokenBudgetTrackerMultiTenantKeyIsolation:
    """Verifies that TokenBudgetTracker strictly isolates metrics and limits per tenant (§23, §49)."""

    def test_cross_tenant_agent_usage_isolated(self) -> None:
        """Two tenants sharing identical agent IDs must not have their token or cost metrics aggregated."""
        tracker = TokenBudgetTracker()

        tracker.consume("tenant_A", "agent_shared", tokens=300, cost_minor_units=30)
        tracker.consume("tenant_B", "agent_shared", tokens=100, cost_minor_units=10)

        # Tenant A's agent usage
        assert tracker.get_usage("tenant_A", "agent_shared") == (300, 30)
        # Tenant B's agent usage
        assert tracker.get_usage("tenant_B", "agent_shared") == (100, 10)

        # Tenant aggregate usage
        assert tracker.get_usage("tenant_A") == (300, 30)
        assert tracker.get_usage("tenant_B") == (100, 10)

    def test_no_cross_tenant_suffix_matching_leakage(self) -> None:
        """Calling get_usage with an entity ID does not aggregate agents across tenants via suffix matching."""
        tracker = TokenBudgetTracker()
        tracker.consume("tenant_01", "agent_common", tokens=250, cost_minor_units=25)
        tracker.consume("tenant_02", "agent_common", tokens=150, cost_minor_units=15)

        # Without tenant_id or passing raw 'agent_common', it does not return the sum (400, 40)
        usage_raw = tracker.get_usage("agent_common")
        assert usage_raw == (0, 0)

        # Strict lookups
        assert tracker.get_usage("tenant_01", "agent_common") == (250, 25)
        assert tracker.get_usage("tenant_02", "agent_common") == (150, 15)

    def test_limits_strictly_partitioned_per_tenant(self) -> None:
        """Setting limits for (tenant_A, agent_1) does not apply to (tenant_B, agent_1)."""
        tracker = TokenBudgetTracker(
            default_limits=BudgetLimits(max_tokens=1000, max_cost_minor_units=100)
        )

        # Restrict agent_1 only in tenant_A
        tracker.set_limits(
            "tenant_A", BudgetLimits(max_tokens=200, max_cost_minor_units=20), agent_id="agent_1"
        )

        # Tenant A agent_1 exceeds limit at 201 tokens
        tracker.consume("tenant_A", "agent_1", tokens=200, cost_minor_units=10)
        assert tracker.has_budget("tenant_A", "agent_1", estimated_tokens=1) is False

        # Tenant B agent_1 retains default limit (1000 tokens)
        tracker.consume("tenant_B", "agent_1", tokens=200, cost_minor_units=10)
        assert tracker.has_budget("tenant_B", "agent_1", estimated_tokens=100) is True

    def test_atomic_acquire_and_release_strictly_tenant_scoped(self) -> None:
        """Acquire and release operate strictly within tenant-partitioned keys."""
        tracker = TokenBudgetTracker(
            default_limits=BudgetLimits(max_tokens=500, max_cost_minor_units=50)
        )

        assert tracker.acquire("t1", "a1", estimated_tokens=300, estimated_cost=30) is True
        assert tracker.get_usage("t1", "a1") == (300, 30)
        assert tracker.get_usage("t2", "a1") == (0, 0)

        # Rollback in t1
        tracker.release("t1", "a1", tokens=300, cost_minor_units=30)
        assert tracker.get_usage("t1", "a1") == (0, 0)


# ===========================================================================
# Feature 30: Approval Repository Multi-Tenant Scoping
# ===========================================================================
class TestFeature30ApprovalRepositoryMultiTenantScoping:
    """Verifies that approval repository implementations enforce mandatory tenant_id on list_pending (§20, §49, §50)."""

    def test_in_memory_approval_repo_list_pending_requires_tenant_id(self) -> None:
        """InMemoryApprovalRepository.list_pending must require tenant_id: str."""
        repo = InMemoryApprovalRepository()

        # Calling without tenant_id must raise TypeError
        with pytest.raises(TypeError):
            repo.list_pending()  # type: ignore[call-arg]

    def test_in_memory_approval_repo_list_pending_isolates_tenants(self) -> None:
        """InMemoryApprovalRepository.list_pending strictly returns approvals for the requested tenant partition."""
        repo = InMemoryApprovalRepository()
        service = ApprovalService(repo)

        act1 = make_action_request(action_id="act_t1", tenant_id="tenant_01", amount_minor=5000)
        act2 = make_action_request(action_id="act_t2", tenant_id="tenant_02", amount_minor=8000)

        app1 = service.create_approval(act1, tenant_id="tenant_01")
        app2 = service.create_approval(act2, tenant_id="tenant_02")

        # Tenant 01 sees only app1
        pending_t1 = repo.list_pending("tenant_01")
        assert len(pending_t1) == 1
        assert pending_t1[0].approval_id == app1.approval_id

        # Tenant 02 sees only app2
        pending_t2 = repo.list_pending("tenant_02")
        assert len(pending_t2) == 1
        assert pending_t2[0].approval_id == app2.approval_id

        # Maintenance method returns all pending across partitions
        all_pending = repo.list_all_pending_for_maintenance()
        assert len(all_pending) == 2
        assert {a.approval_id for a in all_pending} == {app1.approval_id, app2.approval_id}

    def test_sqlalchemy_approval_repo_list_pending_and_scoped_lookups(self) -> None:
        """SqlAlchemyApprovalRepository requires tenant_id on list_pending and scopes save lookups by tenant."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        repo = SqlAlchemyApprovalRepository(session_factory)

        # Calling without tenant_id must raise TypeError
        with pytest.raises(TypeError):
            repo.list_pending()  # type: ignore[call-arg]

        now = datetime.now(UTC)
        app_t1 = Approval(
            approval_id="appr_t1",
            tenant_id="tenant_01",
            binding=ApprovalBinding(
                action_id="act_t1",
                amount=Money(5000, "INR"),
                merchant_id="m1",
                resource_id="r1",
                currency="INR",
                action_version=1,
                tenant_id="tenant_01",
                agent_id="ag1",
                principal_id="usr1",
            ),
            status=ApprovalStatus.PENDING,
            requested_at=now,
        )
        app_t2 = Approval(
            approval_id="appr_t2",
            tenant_id="tenant_02",
            binding=ApprovalBinding(
                action_id="act_t2",
                amount=Money(6000, "INR"),
                merchant_id="m2",
                resource_id="r2",
                currency="INR",
                action_version=1,
                tenant_id="tenant_02",
                agent_id="ag2",
                principal_id="usr2",
            ),
            status=ApprovalStatus.PENDING,
            requested_at=now,
        )

        repo.save("tenant_01", app_t1)
        repo.save("tenant_02", app_t2)

        # Partitioned listing
        assert len(repo.list_pending("tenant_01")) == 1
        assert repo.list_pending("tenant_01")[0].approval_id == "appr_t1"

        assert len(repo.list_pending("tenant_02")) == 1
        assert repo.list_pending("tenant_02")[0].approval_id == "appr_t2"

        assert repo.list_pending("tenant_03") == []

        # Maintenance list returns both
        all_pending = repo.list_all_pending_for_maintenance()
        assert len(all_pending) == 2
        assert {a.approval_id for a in all_pending} == {"appr_t1", "appr_t2"}

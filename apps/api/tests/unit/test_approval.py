"""Unit and property tests for Approval engine, bindings, and decision intelligence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.application.services.approval_service import ApprovalService
from agentpay.domain.action_request import (
    ActionRequest,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import (
    Approval,
    ApprovalBinding,
    ApprovalPolicy,
    ApprovalStatus,
    ApprovalTier,
)
from agentpay.domain.decision import (
    DecisionContext,
    DecisionRecommendation,
    RiskLevel,
)
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidApprovalTransitionError,
)
from agentpay.domain.money import Money


def make_action_request(
    amount_minor: int = 5000,
    version: int = 1,
    tenant_id: str = "tenant_01",
    action_id: str = "act_01",
) -> ActionRequest:
    return ActionRequest(
        action_id=action_id,
        tenant_id=tenant_id,
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_123"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop procurement"),
        amount=Money(amount_minor, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
        version=version,
    )


def test_approval_binding_exact_matching() -> None:
    binding = ApprovalBinding(
        action_id="act_01",
        amount=Money(5000, "INR"),
        merchant_id="prod_123",
        resource_id="prod_123",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
    )
    assert (
        binding.matches(
            action_id="act_01",
            amount=Money(5000, "INR"),
            merchant_id="prod_123",
            resource_id="prod_123",
            currency="INR",
            action_version=1,
            tenant_id="tenant_01",
        )
        is True
    )

    # Amount mismatch raises
    with pytest.raises(ApprovalBindingMismatchError, match="Amount mismatch"):
        binding.assert_exact_match(
            action_id="act_01",
            amount=Money(50000, "INR"),  # ₹50,000 instead of ₹5,000
            merchant_id="prod_123",
            resource_id="prod_123",
            currency="INR",
            action_version=1,
            tenant_id="tenant_01",
        )

    # Action version mismatch raises (prevent approval reuse on mutated action)
    with pytest.raises(ApprovalBindingMismatchError, match="Action version mismatch"):
        binding.assert_exact_match(
            action_id="act_01",
            amount=Money(5000, "INR"),
            merchant_id="prod_123",
            resource_id="prod_123",
            currency="INR",
            action_version=2,
            tenant_id="tenant_01",
        )


def test_approval_state_machine_transitions() -> None:
    binding = ApprovalBinding(
        action_id="act_01",
        amount=Money(5000, "INR"),
        merchant_id="prod_123",
        resource_id="prod_123",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
    )
    appr = Approval(
        approval_id="appr_01",
        tenant_id="tenant_01",
        binding=binding,
        status=ApprovalStatus.PENDING,
    )
    assert appr.is_pending is True

    # Approve
    approved = appr.approve("manager_bob")
    assert approved.is_approved is True
    assert approved.approver_id == "manager_bob"

    # Terminal state cannot transition again
    with pytest.raises(InvalidApprovalTransitionError):
        approved.reject("manager_bob", "Too late")

    # Reject from pending
    rejected = appr.reject("manager_bob", "Budget exceeded")
    assert rejected.status == ApprovalStatus.REJECTED

    with pytest.raises(InvalidApprovalTransitionError):
        rejected.approve("manager_bob")


def test_approval_service_creation_and_binding_verification() -> None:
    repo = InMemoryApprovalRepository()
    service = ApprovalService(approval_repo=repo)

    action = make_action_request(amount_minor=8000)
    approval = service.create_approval(action, tenant_id="tenant_01", reason="Exceeds limit")

    assert approval.status == ApprovalStatus.PENDING
    assert service.verify_binding(approval, action) is True

    # Mutated action (amount increased) must fail verification
    tampered_action = make_action_request(amount_minor=80000)
    with pytest.raises(ApprovalBindingMismatchError):
        service.verify_binding(approval, tampered_action)

    # Grant approval
    granted = service.grant_approval(approval.approval_id, "finance_lead", "tenant_01")
    assert granted.status == ApprovalStatus.APPROVED
    assert repo.get("tenant_01", approval.approval_id) == granted


def test_approval_policy_tiers() -> None:
    policy = ApprovalPolicy(
        tiers=(
            ApprovalTier(threshold_minor=5000, approver_role="MANAGER"),
            ApprovalTier(threshold_minor=50000, approver_role="FINANCE"),
            ApprovalTier(threshold_minor=500000, approver_role="EXECUTIVE"),
        ),
        policy_version="pol_v1",
    )

    t1 = policy.required_tier(2000)
    assert t1 is None

    t2 = policy.required_tier(10000)
    assert t2 is not None and t2.approver_role == "MANAGER"

    t3 = policy.required_tier(75000)
    assert t3 is not None and t3.approver_role == "FINANCE"

    t4 = policy.required_tier(1000000)
    assert t4 is not None and t4.approver_role == "EXECUTIVE"


def test_mock_decision_engine() -> None:
    engine = MockDecisionEngine()

    # Under 200,000 -> LOW risk, ALLOW
    d1 = engine.evaluate(
        DecisionContext(
            action_id="act_01",
            tenant_id="t1",
            agent_id="ag_01",
            amount=Money(150000, "INR"),
        )
    )
    assert d1.recommendation == DecisionRecommendation.ALLOW
    assert d1.risk_level == RiskLevel.LOW

    # 200,001 to 500,000 -> MEDIUM risk, REVIEW
    d2 = engine.evaluate(
        DecisionContext(
            action_id="act_01",
            tenant_id="t1",
            agent_id="ag_01",
            amount=Money(350000, "INR"),
        )
    )
    assert d2.recommendation == DecisionRecommendation.REVIEW
    assert d2.risk_level == RiskLevel.MEDIUM

    # > 2,000,000 -> CRITICAL risk, DENY
    d3 = engine.evaluate(
        DecisionContext(
            action_id="act_01",
            tenant_id="t1",
            agent_id="ag_01",
            amount=Money(2500000, "INR"),
        )
    )
    assert d3.recommendation == DecisionRecommendation.DENY
    assert d3.risk_level == RiskLevel.CRITICAL


def test_cross_tenant_approval_isolation() -> None:
    repo = InMemoryApprovalRepository()
    service = ApprovalService(approval_repo=repo)
    action = make_action_request(tenant_id="tenant_01")

    # Cannot create approval under mismatched tenant
    with pytest.raises(CrossTenantAccessError):
        service.create_approval(action, tenant_id="tenant_02")


@given(
    st.integers(min_value=1, max_value=100000),
    st.integers(min_value=1, max_value=100000),
)
def test_hypothesis_approval_binding_amount_invariance(
    approved_minor: int,
    execution_minor: int,
) -> None:
    binding = ApprovalBinding(
        action_id="act_01",
        amount=Money(approved_minor, "INR"),
        merchant_id="prod_1",
        resource_id="prod_1",
        currency="INR",
        action_version=1,
        tenant_id="t1",
    )
    if approved_minor == execution_minor:
        assert (
            binding.matches(
                action_id="act_01",
                amount=Money(execution_minor, "INR"),
                merchant_id="prod_1",
                resource_id="prod_1",
                currency="INR",
                action_version=1,
                tenant_id="t1",
            )
            is True
        )
    else:
        with pytest.raises(ApprovalBindingMismatchError):
            binding.assert_exact_match(
                action_id="act_01",
                amount=Money(execution_minor, "INR"),
                merchant_id="prod_1",
                resource_id="prod_1",
                currency="INR",
                action_version=1,
                tenant_id="t1",
            )


def test_approval_binding_exact_8_field_matching() -> None:
    """AP-006: Explicit 8-field binding verifies principal_id and tenant_id alongside commerce fields."""
    binding = ApprovalBinding(
        action_id="act_01",
        amount=Money(5000, "INR"),
        merchant_id="merch_01",
        resource_id="prod_123",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
        principal_id="user_alice",
    )
    assert (
        binding.matches(
            action_id="act_01",
            amount=Money(5000, "INR"),
            merchant_id="merch_01",
            resource_id="prod_123",
            currency="INR",
            action_version=1,
            tenant_id="tenant_01",
            principal_id="user_alice",
        )
        is True
    )

    # Principal mismatch raises
    with pytest.raises(
        ApprovalBindingMismatchError,
        match="Principal mismatch: approval bound to 'user_alice', got 'user_bob'",
    ):
        binding.assert_exact_match(
            action_id="act_01",
            amount=Money(5000, "INR"),
            merchant_id="merch_01",
            resource_id="prod_123",
            currency="INR",
            action_version=1,
            tenant_id="tenant_01",
            principal_id="user_bob",
        )

    # Tenant mismatch raises
    with pytest.raises(
        ApprovalBindingMismatchError,
        match="Tenant mismatch: approval bound to 'tenant_01', got 'tenant_02'",
    ):
        binding.assert_exact_match(
            action_id="act_01",
            amount=Money(5000, "INR"),
            merchant_id="merch_01",
            resource_id="prod_123",
            currency="INR",
            action_version=1,
            tenant_id="tenant_02",
            principal_id="user_alice",
        )


def test_approval_service_cross_principal_attack_rejected() -> None:
    """AP-006: User Bob cannot verify or execute an approval bound to User Alice in the same tenant."""
    repo = InMemoryApprovalRepository()
    service = ApprovalService(repo)

    alice_action = ActionRequest(
        action_id="act_alice_01",
        tenant_id="tenant_01",
        principal_id="user_alice",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_123"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="procurement"),
        amount=Money(10000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
        version=1,
    )
    approval = service.create_approval(alice_action, tenant_id="tenant_01")
    assert approval.binding.principal_id == "user_alice"

    # Bob constructs action with identical parameters except principal_id
    bob_action = ActionRequest(
        action_id="act_alice_01",
        tenant_id="tenant_01",
        principal_id="user_bob",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_123"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="procurement"),
        amount=Money(10000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
        version=1,
    )

    with pytest.raises(
        ApprovalBindingMismatchError,
        match="Principal mismatch: approval bound to 'user_alice', got 'user_bob'",
    ):
        service.verify_binding(approval, bob_action)


def test_approval_repository_list_pending() -> None:
    """AP-006: list_pending returns only pending approvals partitioned by tenant."""
    repo = InMemoryApprovalRepository()
    service = ApprovalService(repo)

    act1 = make_action_request(action_id="act_pend_1", amount_minor=5000)
    act2 = make_action_request(action_id="act_pend_2", amount_minor=6000)
    app1 = service.create_approval(act1, tenant_id="tenant_01")
    app2 = service.create_approval(act2, tenant_id="tenant_01")

    # Initially both are pending
    pending = repo.list_pending("tenant_01")
    assert len(pending) == 2
    assert {a.approval_id for a in pending} == {app1.approval_id, app2.approval_id}

    # Grant app1 -> no longer pending
    service.grant_approval(app1.approval_id, approver_id="mgr_01", tenant_id="tenant_01")
    pending_after = repo.list_pending("tenant_01")
    assert len(pending_after) == 1
    assert pending_after[0].approval_id == app2.approval_id

    # Another tenant has 0 pending
    assert repo.list_pending("tenant_02") == []

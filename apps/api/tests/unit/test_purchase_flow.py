"""End-to-End Vertical Slice Integration Tests for AgentPay (§74, §112).

Verifies the 7 core acceptance scenarios:
1. Autonomous purchase flow (under-limit ALLOW -> payment -> order -> audit)
2. Approval-gated flow (over-autonomous REVIEW -> approval binding -> execution)
3. Hard denial flow (over hard ceiling DENY -> abort)
4. Evidence mismatch protection (price/inventory mismatch fails closed before payment)
5. Payment timeout & idempotency recovery (timeout reconciliation without double charge)
6. Multi-tenant isolation (cross-tenant access rejected)
7. Privilege escalation defense (agent cannot elevate limits or permissions)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryEvidenceRepository,
    InMemoryInventoryRepository,
    InMemoryMerchantRepository,
    InMemoryProductRepository,
    InMemoryVariantRepository,
)
from agentpay.adapters.in_memory.identity_repository import (
    InMemoryAgentPermissionsRepository,
    InMemoryAgentRepository,
    InMemoryTenantRepository,
)
from agentpay.adapters.in_memory.payment_repository import (
    InMemoryIdempotencyStore,
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
)
from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.application.use_cases.purchase_flow import (
    PurchaseOrchestrator,
)
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import ApprovalStatus
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    PrivilegeEscalationAttemptError,
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Tenant,
    TenantStatus,
)
from agentpay.domain.money import Money
from agentpay.domain.payments import OrderStatus, PaymentStatus
from agentpay.domain.policy import PolicyDecision, SpendingLimits


def setup_orchestrator(
    tenant_id: str = "tenant_01",
    simulate_timeout: bool = False,
    simulate_decline: bool = False,
) -> tuple[
    PurchaseOrchestrator,
    InMemoryTenantRepository,
    InMemoryAgentRepository,
    InMemoryAgentPermissionsRepository,
    InMemoryMerchantRepository,
    InMemoryProductRepository,
    InMemoryVariantRepository,
    InMemoryInventoryRepository,
    MockPaymentProvider,
    InMemoryAuditLedger,
    InMemoryApprovalRepository,
]:
    tenant_repo = InMemoryTenantRepository()
    agent_repo = InMemoryAgentRepository()
    perms_repo = InMemoryAgentPermissionsRepository()
    merchant_repo = InMemoryMerchantRepository()
    product_repo = InMemoryProductRepository()
    variant_repo = InMemoryVariantRepository()
    inventory_repo = InMemoryInventoryRepository()
    evidence_repo = InMemoryEvidenceRepository()
    approval_repo = InMemoryApprovalRepository()
    payment_repo = InMemoryPaymentRepository()
    order_repo = InMemoryOrderRepository()
    idempotency_store = InMemoryIdempotencyStore()
    audit_ledger = InMemoryAuditLedger()

    payment_provider = MockPaymentProvider(
        simulate_timeout=simulate_timeout,
        simulate_decline=simulate_decline,
    )
    decision_engine = MockDecisionEngine()
    auth_service = AgentAuthorizationService(
        tenant_repo=tenant_repo,
        agent_repo=agent_repo,
        permissions_repo=perms_repo,
    )
    approval_service = ApprovalService(approval_repo=approval_repo)

    orchestrator = PurchaseOrchestrator(
        tenant_repo=tenant_repo,
        agent_repo=agent_repo,
        permissions_repo=perms_repo,
        auth_service=auth_service,
        merchant_repo=merchant_repo,
        product_repo=product_repo,
        variant_repo=variant_repo,
        inventory_repo=inventory_repo,
        evidence_repo=evidence_repo,
        decision_engine=decision_engine,
        approval_service=approval_service,
        approval_repo=approval_repo,
        payment_provider=payment_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        idempotency_store=idempotency_store,
        audit_ledger=audit_ledger,
    )

    # Seed baseline tenant, agent, merchant, product, variant, inventory
    now = datetime.now(UTC)
    tenant_repo.save(Tenant(tenant_id=tenant_id, name="Acme Org", status=TenantStatus.ACTIVE))

    agent = Agent(
        agent_id="agent_01",
        tenant_id=tenant_id,
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        scopes=("commerce:read", "commerce:write"),
        created_at=now,
        expires_at=now + timedelta(days=30),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save(tenant_id, agent)

    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=2000000,  # ₹20,000 hard cap (§112 Scenario 3)
            daily_minor=5000000,
            requires_approval_above_minor=200000,  # ₹2,000 autonomous threshold (§112 Scenario 2)
        ),
        allowed_categories=("electronics", "computers"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save(tenant_id, perms)

    merchant = Merchant(
        merchant_id="merch_01",
        tenant_id=tenant_id,
        name="Official Hardware Store",
        status=MerchantStatus.ACTIVE,
    )
    merchant_repo.save(tenant_id, merchant)

    product = Product(
        product_id="prod_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Pro Laptop",
        category="computers",
        description="High spec developer machine",
    )
    product_repo.save(tenant_id, product)

    variant = ProductVariant(
        variant_id="var_01",
        product_id="prod_01",
        sku="SKU-LAPTOP-01",
        name="16GB RAM",
        price=Money(129900, "INR"),  # ₹1,299
    )
    variant_repo.save(tenant_id, variant)

    inventory = Inventory(
        variant_id="var_01",
        tenant_id=tenant_id,
        available=10,
        reserved=0,
    )
    inventory_repo.save(tenant_id, inventory)

    return (
        orchestrator,
        tenant_repo,
        agent_repo,
        perms_repo,
        merchant_repo,
        product_repo,
        variant_repo,
        inventory_repo,
        payment_provider,
        audit_ledger,
        approval_repo,
    )


def test_scenario_1_autonomous_purchase_allow() -> None:
    """Scenario 1: ₹1,299 requested, limit ₹2,000 -> ALLOW -> executes order and audit trail (§112)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        inventory_repo,
        _,
        audit_ledger,
        _,
    ) = setup_orchestrator()

    action = ActionRequest(
        action_id="act_allow_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop purchase"),
        amount=Money(129900, "INR"),  # ₹1,299 <= ₹2,000 autonomous limit
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
    )

    assert result.status == ActionStatus.COMPLETED
    assert result.decision == PolicyDecision.ALLOW
    assert result.order is not None
    assert result.order.status == OrderStatus.CONFIRMED
    assert result.payment is not None
    assert result.payment.status == PaymentStatus.CAPTURED

    # Verify inventory was reserved
    inv = inventory_repo.get("tenant_01", "var_01")
    assert inv is not None and inv.reserved == 1

    # Verify tamper-evident audit ledger contains events
    events = audit_ledger.get_events("tenant_01", action_id="act_allow_01")
    event_types = [e.event_type for e in events]
    assert AuditEventType.ACTION_REQUESTED in event_types
    assert AuditEventType.POLICY_EVALUATED in event_types
    assert AuditEventType.PAYMENT_CREATED in event_types
    assert AuditEventType.ORDER_CREATED in event_types
    assert audit_ledger.verify_chain("tenant_01") is True


def test_scenario_2_approval_gated_flow() -> None:
    """Scenario 2: ₹8,000 requested, limit ₹2,000 -> REVIEW -> Approval created -> Approved -> Executes (§112)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        variant_repo,
        inventory_repo,
        _,
        _audit_ledger,
        approval_repo,
    ) = setup_orchestrator()

    # Create high-value variant ₹8,000 (800000 paise)
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_high",
            product_id="prod_01",
            sku="SKU-HIGH",
            name="Maxed Spec",
            price=Money(800000, "INR"),
        ),
    )
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_high",
            tenant_id="tenant_01",
            available=5,
            reserved=0,
        ),
    )

    action = ActionRequest(
        action_id="act_review_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="high spec purchase"),
        amount=Money(800000, "INR"),  # ₹8,000 > ₹2,000 autonomous limit
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    # First attempt: evaluates to REVIEW and yields Approval request
    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_high",
        quantity=1,
    )

    assert result.status == ActionStatus.AWAITING_APPROVAL
    assert result.decision == PolicyDecision.REVIEW
    assert result.approval is not None
    assert result.approval.status == ApprovalStatus.PENDING

    # Human manager grants approval
    approval_service = ApprovalService(approval_repo=approval_repo)
    approved_record = approval_service.grant_approval(
        approval_id=result.approval.approval_id,
        approver_id="manager_dave",
        tenant_id="tenant_01",
    )
    assert approved_record.status == ApprovalStatus.APPROVED

    # Re-executing with approved binding completes order
    approved_result = orchestrator.execute_approved_purchase(
        approval_id=result.approval.approval_id,
        action=action,
        tenant_id="tenant_01",
        variant_id="var_high",
        quantity=1,
    )

    assert approved_result.status == ActionStatus.COMPLETED
    assert approved_result.order is not None
    assert approved_result.order.status == OrderStatus.CONFIRMED

    # Attempting to use the approval on a tampered action (₹80,000) raises binding mismatch
    tampered_action = ActionRequest(
        action_id="act_review_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="tampered amount"),
        amount=Money(8000000, "INR"),  # ₹80,000
        authorization_mode=AuthorizationMode.SUPERVISED,
    )
    with pytest.raises(ApprovalBindingMismatchError):
        orchestrator.execute_approved_purchase(
            approval_id=result.approval.approval_id,
            action=tampered_action,
            tenant_id="tenant_01",
            variant_id="var_high",
        )


def test_scenario_3_hard_denial_exceeding_ceiling() -> None:
    """Scenario 3: ₹50,000 requested, hard cap ₹20,000 -> DENY (§112)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        variant_repo,
        inventory_repo,
        _,
        _,
        _,
    ) = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_ultra",
            product_id="prod_01",
            sku="SKU-ULTRA",
            name="Ultra Server",
            price=Money(5000000, "INR"),  # ₹50,000
        ),
    )
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_ultra",
            tenant_id="tenant_01",
            available=5,
            reserved=0,
        ),
    )

    action = ActionRequest(
        action_id="act_deny_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="server purchase"),
        amount=Money(5000000, "INR"),  # ₹50,000 > ₹20,000 hard ceiling
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_ultra",
        quantity=1,
    )

    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert "exceeds the per-transaction maximum" in (result.rejection_reason or "")
    assert result.order is None
    assert result.payment is None


def test_scenario_4_evidence_conflict_price_manipulation() -> None:
    """Scenario 4: Agent asserts ₹1,499, merchant is ₹1,999 -> PRICE_MISMATCH blocks execution (§112)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        variant_repo,
        _,
        _,
        audit_ledger,
        _,
    ) = setup_orchestrator()

    # Merchant catalog price is ₹1,999
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_conflict",
            product_id="prod_01",
            sku="SKU-MISMATCH",
            name="Monitor",
            price=Money(199900, "INR"),
        ),
    )

    # Agent requests action asserting ₹1,499
    action = ActionRequest(
        action_id="act_mismatch_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="monitor purchase"),
        amount=Money(149900, "INR"),  # ₹1,499 != ₹1,999
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_conflict",
        quantity=1,
    )

    assert result.status == ActionStatus.FAILED
    assert len(result.conflicts) > 0
    assert result.conflicts[0].field_name == "price"
    assert "Evidence conflict detected" in (result.rejection_reason or "")
    assert result.payment is None
    assert result.order is None

    # Audit confirms CONFLICT_DETECTED event
    events = audit_ledger.get_events("tenant_01", action_id="act_mismatch_01")
    assert any(e.event_type == AuditEventType.CONFLICT_DETECTED for e in events)


def test_scenario_5_payment_timeout_reconciliation() -> None:
    """Scenario 5: Payment timeout yields UNKNOWN -> safe reconciliation without duplicate charge (§43, §112)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        payment_provider,
        _,
        _,
    ) = setup_orchestrator(simulate_timeout=True)

    action = ActionRequest(
        action_id="act_timeout_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="test timeout"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
    )

    assert result.status == ActionStatus.FAILED
    assert result.payment is not None
    assert result.payment.status == PaymentStatus.UNKNOWN
    assert "pending reconciliation" in (result.rejection_reason or "")

    # Reconcile timeout state directly with provider (§43)
    reconciled = payment_provider.reconcile_timeout("tenant_01", result.payment.attempt_id)
    assert reconciled.status == PaymentStatus.CAPTURED
    assert reconciled.is_successful is True


def test_scenario_6_cross_tenant_access_denial() -> None:
    """Scenario 6: Tenant A attempts action on Tenant B resource -> 403 Forbidden / Denied (§50)."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = setup_orchestrator(tenant_id="tenant_01")

    # Action has tenant_02 in context
    action = ActionRequest(
        action_id="act_crosstenant_01",
        tenant_id="tenant_02",  # Belongs to tenant_02
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="cross-tenant breach attempt"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    with pytest.raises(CrossTenantAccessError):
        orchestrator.execute_purchase(
            action=action,
            tenant_id="tenant_01",  # Context is tenant_01
            variant_id="var_01",
        )


def test_scenario_7_privilege_escalation_denial() -> None:
    """Scenario 7: Malicious agent attempts to widen its own limits or perform unauthorized admin action (§15, §112)."""
    (
        _,
        _,
        agent_repo,
        perms_repo,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = setup_orchestrator(tenant_id="tenant_01")

    agent = agent_repo.get("tenant_01", "agent_01")
    assert agent is not None
    current_perms = perms_repo.get("tenant_01", "agent_01")
    assert current_perms is not None

    # Agent attempts to widen its own limits from ₹20,000 to ₹1,000,000
    elevated_limits = SpendingLimits(
        per_transaction_minor=100000000,  # ₹1,000,000
        daily_minor=500000000,
    )
    proposed_perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id="tenant_01",
        capabilities=current_perms.capabilities,
        limits=elevated_limits,
    )

    auth_service = AgentAuthorizationService(permissions_repo=perms_repo)

    # Calling update_agent_permissions as caller_is_agent=True raises PrivilegeEscalationAttemptError
    with pytest.raises(PrivilegeEscalationAttemptError):
        auth_service.update_agent_permissions(
            tenant_id="tenant_01",
            agent_id="agent_01",
            permissions=proposed_perms,
            caller_principal_id="agent_01",
            caller_is_agent=True,
        )

    # Attempting to widen limits directly raises PrivilegeEscalationAttemptError
    with pytest.raises(PrivilegeEscalationAttemptError):
        current_perms.assert_no_escalation(proposed_perms)

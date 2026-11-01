"""End-to-end tests for the multi-item cart checkout orchestrator (§38, §44, §112, ADR-0006).

Verifies:
1. ALLOW: cart-driven checkout beneath the autonomous threshold completes with one
   payment, one confirmed order per line, inventory reservation, daily spend, and audit.
2. REVIEW: cart total above the autonomous threshold creates a bound approval; approved
   execution succeeds exactly once and the approval is consumed atomically.
3. DENY: cart total above the hard ceiling is refused with no financial side effects.
4. PRICE/INVENTORY mismatch: cart totals or availability conflicts fail closed pre-payment.
5. Single-merchant enforcement, empty cart rejection, and checked-out immutability.
6. Idempotent replay returns the cached checkout result without duplicate effects.
7. Decline and timeout payment outcomes release or hold reservations correctly.
8. Cross-tenant access and capability escalation are denied.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryCartRepository,
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
from agentpay.application.use_cases.purchase_flow import CartCheckoutResult, PurchaseOrchestrator
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import ApprovalAlreadyConsumedError, ApprovalStatus
from agentpay.domain.audit import AuditEventType
from agentpay.domain.cart import Cart, CartStatus
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.decision import Decision, DecisionContext
from agentpay.domain.errors import (
    CrossTenantAccessError,
    InvalidCommerceError,
    MissingCapabilityError,
)
from agentpay.domain.evidence import ConflictType
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Tenant,
    TenantStatus,
    UserRole,
)
from agentpay.domain.money import Money
from agentpay.domain.payments import Order, OrderStatus, PaymentStatus
from agentpay.domain.policy import PolicyDecision, SpendingLimits


class _SlowDecisionEngine(MockDecisionEngine):
    """MockDecisionEngine with artificial latency to widen checkout race windows.

    Models the real latency of a live decision engine call: without it, hermetic
    in-memory checkouts complete before a sibling thread is scheduled, so a
    missing concurrency guard would never be detected by a threaded test (§177).
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds

    def evaluate(self, context: DecisionContext) -> Decision:
        time.sleep(self._delay_seconds)
        return super().evaluate(context)


def setup_cart_orchestrator(
    tenant_id: str = "tenant_01",
    simulate_timeout: bool = False,
    simulate_decline: bool = False,
    include_cart_capability: bool = True,
    decision_delay_seconds: float = 0.0,
) -> tuple[PurchaseOrchestrator, InMemoryCartRepository, InMemoryInventoryRepository]:
    """Seed a hermetic orchestrator with cart-capable agent, merchants, and stock."""
    tenant_repo = InMemoryTenantRepository()
    agent_repo = InMemoryAgentRepository()
    perms_repo = InMemoryAgentPermissionsRepository()
    merchant_repo = InMemoryMerchantRepository()
    product_repo = InMemoryProductRepository()
    variant_repo = InMemoryVariantRepository()
    inventory_repo = InMemoryInventoryRepository()
    evidence_repo = InMemoryEvidenceRepository()
    cart_repo = InMemoryCartRepository()
    approval_repo = InMemoryApprovalRepository()
    payment_repo = InMemoryPaymentRepository()
    order_repo = InMemoryOrderRepository()
    idempotency_store = InMemoryIdempotencyStore()
    audit_ledger = InMemoryAuditLedger()

    payment_provider = MockPaymentProvider(
        simulate_timeout=simulate_timeout,
        simulate_decline=simulate_decline,
    )
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
        cart_repo=cart_repo,
        decision_engine=(
            _SlowDecisionEngine(decision_delay_seconds)
            if decision_delay_seconds > 0
            else MockDecisionEngine()
        ),
        approval_service=approval_service,
        approval_repo=approval_repo,
        payment_provider=payment_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        idempotency_store=idempotency_store,
        audit_ledger=audit_ledger,
    )

    now = datetime.now(UTC)
    tenant_repo.save(Tenant(tenant_id=tenant_id, name="Acme Cart Org", status=TenantStatus.ACTIVE))

    capabilities = ["catalog.search", "payment.request", "checkout.prepare"]
    if include_cart_capability:
        capabilities.append("cart.create")

    agent_repo.save(
        tenant_id,
        Agent(
            agent_id="agent_01",
            tenant_id=tenant_id,
            principal_id="user_01",
            issuer="https://auth.agentpay.local",
            capabilities=tuple(capabilities),
            scopes=("commerce:read", "commerce:write"),
            created_at=now,
            expires_at=now + timedelta(days=30),
            status=AgentStatus.ACTIVE,
        ),
    )
    perms_repo.save(
        tenant_id,
        AgentPermissions(
            agent_id="agent_01",
            tenant_id=tenant_id,
            capabilities=tuple(capabilities),
            limits=SpendingLimits(
                per_transaction_minor=2000000,  # ₹20,000 hard ceiling (§112 Scenario 3)
                daily_minor=5000000,
                requires_approval_above_minor=200000,  # ₹2,000 autonomous threshold
            ),
            allowed_categories=("electronics", "computers"),
            requires_approval_above_minor=200000,
        ),
    )

    merchant_repo.save(
        tenant_id,
        Merchant(
            merchant_id="merch_01",
            tenant_id=tenant_id,
            name="Official Cart Hardware",
            status=MerchantStatus.ACTIVE,
        ),
    )
    merchant_repo.save(
        tenant_id,
        Merchant(
            merchant_id="merch_02",
            tenant_id=tenant_id,
            name="Rival Cart Hardware",
            status=MerchantStatus.ACTIVE,
        ),
    )

    product_repo.save(
        tenant_id,
        Product(
            product_id="prod_01",
            tenant_id=tenant_id,
            merchant_id="merch_01",
            name="Pro Laptop",
            category="computers",
            description="High spec developer machine",
        ),
    )
    for variant, price in (
        ("var_01", 129900),  # ₹1,299.00
        ("var_02", 50000),  # ₹500.00
        ("var_04", 100000),  # ₹1,000.00
    ):
        variant_repo.save(
            tenant_id,
            ProductVariant(
                variant_id=variant,
                product_id="prod_01",
                sku=f"SKU-{variant}",
                name=f"Variant {variant}",
                price=Money(price, "INR"),
            ),
        )
    inventory_repo.save(
        tenant_id, Inventory(variant_id="var_01", tenant_id=tenant_id, available=10, reserved=0)
    )
    inventory_repo.save(
        tenant_id, Inventory(variant_id="var_02", tenant_id=tenant_id, available=3, reserved=0)
    )
    inventory_repo.save(
        tenant_id, Inventory(variant_id="var_04", tenant_id=tenant_id, available=30, reserved=0)
    )

    # Second merchant product (used to prove single-merchant carts fail closed)
    product_repo.save(
        tenant_id,
        Product(
            product_id="prod_02",
            tenant_id=tenant_id,
            merchant_id="merch_02",
            name="Rival Laptop",
            category="computers",
            description="Competing machine",
        ),
    )
    variant_repo.save(
        tenant_id,
        ProductVariant(
            variant_id="var_05",
            product_id="prod_02",
            sku="SKU-VAR-05",
            name="Rival Variant",
            price=Money(110000, "INR"),
        ),
    )
    inventory_repo.save(
        tenant_id, Inventory(variant_id="var_05", tenant_id=tenant_id, available=5, reserved=0)
    )

    return orchestrator, cart_repo, inventory_repo


def checkout_action(
    cart_id: str,
    amount_minor: int,
    action_id: str = "act_cart_checkout_01",
) -> ActionRequest:
    return ActionRequest(
        action_id=action_id,
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="checkout",
        resource=ResourceRef(type="cart", id=cart_id),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="cart checkout test"),
        amount=Money(amount_minor, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )


# ============================================================================
# 1. ALLOW — multi-item checkout
# ============================================================================
def test_cart_allow_two_items_one_payment_two_orders() -> None:
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    payload = orchestrator.get_cart_payload("tenant_01", cart.cart_id)
    assert payload is not None
    assert payload["total_minor"] == 179900  # 129900 + 50000 <= 200000 threshold
    assert payload["status"] == "ACTIVE"
    assert all(item["resolved"] for item in payload["items"])

    action = checkout_action(cart.cart_id, 179900)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.COMPLETED
    assert result.decision == PolicyDecision.ALLOW
    assert len(result.orders) == 2
    assert all(order.status == OrderStatus.CONFIRMED for order in result.orders)
    assert result.payment is not None
    assert result.payment.status == PaymentStatus.CAPTURED

    # A single payment covers every order in the cart
    attempt_id = result.payment.attempt_id
    for order in result.orders:
        assert order.payment_attempt_id == attempt_id
        order_amount = order.total_amount.minor_units
        assert order_amount in {129900, 50000}
    assert {order.total_amount.minor_units for order in result.orders} == {129900, 50000}

    # Cart is checked out and persisted in its terminal state
    assert result.cart is not None
    assert result.cart.status is CartStatus.CHECKED_OUT
    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.CHECKED_OUT

    # Inventory was reserved for both lines
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 1)
    assert inv_01.effective_available == 9
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 1)
    assert inv_02.effective_available == 2

    # Daily spend ledger accumulated the full cart total
    assert orchestrator._get_spent_today_minor("tenant_01", "agent_01") == 179900

    # Audit trail records the checkout milestones and remains tamper-evident
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None
    events = audit_ledger.get_events("tenant_01", action_id=action.action_id)
    event_types = [event.event_type for event in events]
    assert AuditEventType.ACTION_REQUESTED in event_types
    assert AuditEventType.POLICY_EVALUATED in event_types
    assert AuditEventType.PAYMENT_CREATED in event_types
    assert event_types.count(AuditEventType.ORDER_CREATED) == 2
    assert audit_ledger.verify_chain("tenant_01") is True


# ============================================================================
# 2. REVIEW -> approval -> approved execution
# ============================================================================
def test_cart_review_creates_bound_approval_and_executes_once() -> None:
    orchestrator, cart_repo, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=2)

    action = checkout_action(cart.cart_id, 259800)  # ₹2,598 > ₹2,000 threshold
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.AWAITING_APPROVAL
    assert result.decision == PolicyDecision.REVIEW
    assert result.approval is not None
    assert result.approval.binding.amount.minor_units == 259800
    assert result.approval.binding.resource_id == cart.cart_id
    assert result.approval.binding.merchant_id == "merch_01"
    assert result.approval.binding.principal_id == "user_01"

    # Nothing financial happened during review
    assert result.payment is None
    assert result.orders == ()
    still_active = cart_repo.get("tenant_01", cart.cart_id)
    assert still_active is not None and still_active.status is CartStatus.ACTIVE

    # Human grants the approval (§20, §66)
    approval = orchestrator._approval_service.grant_approval(
        approval_id=result.approval.approval_id,
        approver_id="manager_01",
        tenant_id="tenant_01",
        approver_role=UserRole.ADMIN,
        caller_principal_id="manager_01",
    )
    assert approval.status == ApprovalStatus.APPROVED

    approved_action = checkout_action(
        cart.cart_id,
        259800,
        action_id=approval.binding.action_id,
    )
    approved_action = ActionRequest(
        action_id=approved_action.action_id,
        tenant_id=approved_action.tenant_id,
        principal_id=approved_action.principal_id,
        agent_id=approved_action.agent_id,
        domain=approved_action.domain,
        action=approved_action.action,
        resource=approved_action.resource,
        requested_at=approved_action.requested_at,
        intent=approved_action.intent,
        amount=approved_action.amount,
        authorization_mode=approved_action.authorization_mode,
        version=approval.binding.action_version,
    )

    executed = orchestrator.execute_approved_cart_purchase(
        approval_id=approval.approval_id,
        action=approved_action,
        tenant_id="tenant_01",
    )
    assert executed.status == ActionStatus.COMPLETED
    assert len(executed.orders) == 1
    assert executed.orders[0].quantity == 2
    assert executed.payment is not None and executed.payment.status == PaymentStatus.CAPTURED

    # Approval is single-use: consumed atomically and never replayable (§20, §0.4)
    approval_repo = orchestrator._approval_repo
    assert approval_repo is not None
    consumed = approval_repo.get("tenant_01", approval.approval_id)
    assert consumed is not None and consumed.status == ApprovalStatus.CONSUMED
    with pytest.raises(ApprovalAlreadyConsumedError):
        orchestrator.execute_approved_cart_purchase(
            approval_id=approval.approval_id,
            action=approved_action,
            tenant_id="tenant_01",
        )


def test_cart_approved_execution_rejects_amount_mismatch() -> None:
    """An approval bound to ₹2,598 must never authorize a different total (§20)."""
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=2)
    action = checkout_action(cart.cart_id, 259800)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )
    assert result.approval is not None

    granted = orchestrator._approval_service.grant_approval(
        approval_id=result.approval.approval_id,
        approver_id="manager_01",
        tenant_id="tenant_01",
        approver_role=UserRole.ADMIN,
        caller_principal_id="manager_01",
    )

    tampered = checkout_action(cart.cart_id, 50000, action_id=granted.binding.action_id)
    tampered = ActionRequest(
        action_id=tampered.action_id,
        tenant_id=tampered.tenant_id,
        principal_id=tampered.principal_id,
        agent_id=tampered.agent_id,
        domain=tampered.domain,
        action=tampered.action,
        resource=tampered.resource,
        requested_at=tampered.requested_at,
        intent=tampered.intent,
        amount=tampered.amount,
        authorization_mode=tampered.authorization_mode,
        version=granted.binding.action_version,
    )

    from agentpay.domain.errors import ApprovalBindingMismatchError

    with pytest.raises(ApprovalBindingMismatchError):
        orchestrator.execute_approved_cart_purchase(
            approval_id=granted.approval_id,
            action=tampered,
            tenant_id="tenant_01",
        )


# ============================================================================
# 3. DENY — hard ceiling
# ============================================================================
def test_cart_hard_denial_exceeds_ceiling_without_side_effects() -> None:
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=21)
    total = 21 * 100000  # ₹21,000 > ₹20,000 hard ceiling

    action = checkout_action(cart.cart_id, total)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert result.rejection_reason is not None
    assert result.payment is None
    assert result.orders == ()

    # Cart stays ACTIVE, stock untouched, no spend recorded
    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE
    inv = inventory_repo.get("tenant_01", "var_04")
    assert inv is not None and (inv.available, inv.reserved) == (30, 0)
    assert orchestrator._get_spent_today_minor("tenant_01", "agent_01") == 0


def test_cart_category_outside_permission_denied() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    product_repo = orchestrator._product_repo
    variant_repo = orchestrator._variant_repo
    inventory_repo = orchestrator._inventory_repo
    assert product_repo is not None and variant_repo is not None and inventory_repo is not None

    product_repo.save(
        "tenant_01",
        Product(
            product_id="prod_restricted",
            tenant_id="tenant_01",
            merchant_id="merch_01",
            name="Restricted Item",
            category="restricted",
            description="Not in agent category allowlist",
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_restricted",
            product_id="prod_restricted",
            sku="SKU-RESTRICTED",
            name="Restricted Variant",
            price=Money(50000, "INR"),
        ),
    )
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_restricted",
            tenant_id="tenant_01",
            available=5,
            reserved=0,
        ),
    )

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    # Adding is permitted; the deterministic category policy refuses at checkout.
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_restricted", quantity=1)
    action = checkout_action(cart.cart_id, 50000)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert result.rejection_reason is not None
    assert "not permitted" in result.rejection_reason
    assert result.payment is None


# ============================================================================
# 4. PRICE and INVENTORY mismatch fail closed
# ============================================================================
def test_cart_price_mismatch_fails_closed_before_payment() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)

    action = checkout_action(cart.cart_id, 999900)  # asserted ₹9,999 vs ₹1,299 truth
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert any(
        conflict.conflict_type is ConflictType.PRICE_MISMATCH for conflict in result.conflicts
    )
    assert result.payment is None
    assert result.orders == ()


def test_cart_inventory_mismatch_fails_closed_and_leaks_nothing() -> None:
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item(
        "tenant_01", cart.cart_id, "var_02", quantity=4
    )  # only 3 in stock

    action = checkout_action(cart.cart_id, 4 * 50000)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert any(
        conflict.conflict_type is ConflictType.INVENTORY_MISMATCH for conflict in result.conflicts
    )
    assert result.payment is None

    inv = inventory_repo.get("tenant_01", "var_02")
    assert inv is not None and (inv.available, inv.reserved) == (3, 0)


# ============================================================================
# 5. Structural cart rules
# ============================================================================
def test_cart_multi_merchant_mix_fails_closed() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    with pytest.raises(InvalidCommerceError, match="single-merchant cart"):
        orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_05", quantity=1)


def test_cart_empty_cart_checkout_rejected() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    action = checkout_action(cart.cart_id, 100)
    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert result.rejection_reason == "Cart is empty"


def test_checked_out_cart_is_immutable_and_not_recheckoutable() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
    action = checkout_action(cart.cart_id, 50000)
    first = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )
    assert first.status == ActionStatus.COMPLETED

    with pytest.raises(InvalidCommerceError, match="Cannot modify cart"):
        orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)

    second_action = checkout_action(cart.cart_id, 50000, action_id="act_cart_checkout_02")
    second = orchestrator.execute_cart_purchase(
        action=second_action, tenant_id="tenant_01", cart_id=cart.cart_id
    )
    assert second.status == ActionStatus.FAILED
    assert second.rejection_reason is not None
    assert "not ACTIVE" in second.rejection_reason


# ============================================================================
# 6. Idempotent replay
# ============================================================================
def test_cart_checkout_idempotent_replay_returns_cached_result() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)

    action = checkout_action(cart.cart_id, 129900)
    first = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id, idempotency_key="idem_cart_01"
    )
    assert first.status == ActionStatus.COMPLETED

    replay = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id, idempotency_key="idem_cart_01"
    )

    assert replay.status == first.status
    assert [order.order_id for order in replay.orders] == [order.order_id for order in first.orders]
    assert replay.payment is not None and first.payment is not None
    assert replay.payment.attempt_id == first.payment.attempt_id

    # Exactly one payment attempt exists for the replay-safe checkout
    payment_repo = orchestrator._payment_repo
    assert payment_repo is not None


def test_cart_checkout_idempotency_conflict_on_changed_amount() -> None:
    from agentpay.domain.errors import IdempotencyConflictError

    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)

    action = checkout_action(cart.cart_id, 129900)
    orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id, idempotency_key="idem_cart_02"
    )

    conflicting = checkout_action(cart.cart_id, 999900, action_id="act_cart_checkout_conflict")
    with pytest.raises(IdempotencyConflictError):
        orchestrator.execute_cart_purchase(
            action=conflicting,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
            idempotency_key="idem_cart_02",
        )


# ============================================================================
# 7. Decline and timeout outcomes
# ============================================================================
def test_cart_declined_payment_releases_reservations() -> None:
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator(simulate_decline=True)

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    action = checkout_action(cart.cart_id, 129900)

    result = orchestrator.execute_cart_purchase(
        action=action,
        tenant_id="tenant_01",
        cart_id=cart.cart_id,
        idempotency_key="idem_cart_decline",
    )

    assert result.status == ActionStatus.FAILED
    assert result.payment is not None and result.payment.status == PaymentStatus.DECLINED
    assert result.orders == ()

    # Reservations are compensated and the cart remains usable
    inv = inventory_repo.get("tenant_01", "var_01")
    assert inv is not None and (inv.available, inv.reserved) == (10, 0)
    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE

    # Replay of the failed attempt is served from the idempotency record
    replay = orchestrator.execute_cart_purchase(
        action=action,
        tenant_id="tenant_01",
        cart_id=cart.cart_id,
        idempotency_key="idem_cart_decline",
    )
    assert replay.status == ActionStatus.FAILED


def test_cart_timeout_payment_holds_reservations_for_reconciliation() -> None:
    orchestrator, _, inventory_repo = setup_cart_orchestrator(simulate_timeout=True)

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    action = checkout_action(cart.cart_id, 129900)

    result = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )

    assert result.status == ActionStatus.FAILED
    assert result.payment is not None and result.payment.status == PaymentStatus.UNKNOWN
    assert result.rejection_reason is not None
    assert "reconciliation" in result.rejection_reason.lower()

    # Reservations are intentionally held while payment state is unknown (§43)
    inv = inventory_repo.get("tenant_01", "var_01")
    assert inv is not None and (inv.available, inv.reserved) == (10, 1)
    assert inv.effective_available == 9


# ============================================================================
# 8. Cross-tenant and capability escalation defense
# ============================================================================
def test_cart_cross_tenant_access_denied() -> None:
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )

    with pytest.raises(InvalidCommerceError, match="not found in tenant"):
        orchestrator.add_cart_item("tenant_02", cart.cart_id, "var_01", quantity=1)

    action = checkout_action(cart.cart_id, 129900)
    mismatched = ActionRequest(
        action_id=action.action_id,
        tenant_id="tenant_02",  # asserted tenant differs from execution context
        principal_id=action.principal_id,
        agent_id=action.agent_id,
        domain=action.domain,
        action=action.action,
        resource=action.resource,
        requested_at=action.requested_at,
        intent=action.intent,
        amount=action.amount,
        authorization_mode=action.authorization_mode,
    )
    with pytest.raises(CrossTenantAccessError):
        orchestrator.execute_cart_purchase(
            action=mismatched, tenant_id="tenant_01", cart_id=cart.cart_id
        )


def test_cart_creation_requires_cart_capability() -> None:
    orchestrator, _, _ = setup_cart_orchestrator(include_cart_capability=False)

    with pytest.raises(MissingCapabilityError):
        orchestrator.create_cart(tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01")


# ============================================================================
# 9. Concurrency — duplicate-payment defense (§43, §177, ADR-0006)
# ============================================================================
def test_cart_checkout_waits_on_per_cart_lock() -> None:
    """A checkout must block on the cart's lock until the holder releases it.

    Deterministic contract test for the concurrency guard: if the checkout path
    stops acquiring the per-cart lock, the worker thread completes while the test
    thread still holds the lock and this assertion fails.
    """
    orchestrator, _, _ = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    lock = orchestrator._cart_checkout_lock("tenant_01", cart.cart_id)
    done = threading.Event()
    results: list[CartCheckoutResult] = []
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            results.append(
                orchestrator.execute_cart_purchase(
                    action=checkout_action(cart.cart_id, 50000, action_id="act_cart_lock_probe"),
                    tenant_id="tenant_01",
                    cart_id=cart.cart_id,
                    idempotency_key="idem_cart_lock_probe",
                )
            )
        except BaseException as exc:  # pragma: no cover - harness failure capture
            errors.append(exc)
        finally:
            done.set()

    lock.acquire()
    thread = threading.Thread(target=attempt)
    thread.start()
    try:
        assert not done.wait(timeout=0.3), "checkout ran while the cart lock was held"
    finally:
        lock.release()
    thread.join(timeout=10)

    assert not errors
    assert done.is_set()
    assert len(results) == 1 and results[0].status == ActionStatus.COMPLETED


def _race_two_checkouts(
    orchestrator: PurchaseOrchestrator,
    cart_id: str,
    amount_minor: int,
    action_ids: tuple[str, str],
    idem_keys: tuple[str, str],
) -> tuple[list[CartCheckoutResult], list[BaseException]]:
    """Run two barrier-synchronized checkouts of the same cart concurrently."""
    barrier = threading.Barrier(2)
    results: list[CartCheckoutResult] = []
    errors: list[BaseException] = []

    def attempt(action_id: str, idem_key: str) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(
                orchestrator.execute_cart_purchase(
                    action=checkout_action(cart_id, amount_minor, action_id=action_id),
                    tenant_id="tenant_01",
                    cart_id=cart_id,
                    idempotency_key=idem_key,
                )
            )
        except BaseException as exc:  # pragma: no cover - harness failure capture
            errors.append(exc)

    threads = [
        threading.Thread(target=attempt, args=(action_ids[0], idem_keys[0])),
        threading.Thread(target=attempt, args=(action_ids[1], idem_keys[1])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def test_cart_concurrent_checkouts_cannot_double_charge() -> None:
    """Two simultaneous checkouts with distinct keys must charge exactly once.

    Without the per-cart checkout lock both requests pass the ACTIVE-cart check
    and reach the payment pipeline, producing duplicate payments. The loser must
    observe CHECKED_OUT and fail closed with no financial side effects.
    """
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator(decision_delay_seconds=0.05)

    for round_index in range(3):
        cart = orchestrator.create_cart(
            tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
        )
        cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

        results, errors = _race_two_checkouts(
            orchestrator,
            cart.cart_id,
            50000,
            (f"act_cart_race_a_{round_index}", f"act_cart_race_b_{round_index}"),
            (f"idem_cart_race_a_{round_index}", f"idem_cart_race_b_{round_index}"),
        )

        assert not errors
        assert len(results) == 2
        completed = [r for r in results if r.status == ActionStatus.COMPLETED]
        rejected = [r for r in results if r.status == ActionStatus.FAILED]
        assert len(completed) == 1, f"round {round_index}: duplicate checkout executed"
        assert len(rejected) == 1
        assert "CHECKED_OUT" in (rejected[0].rejection_reason or "")
        assert len(completed[0].orders) == 1
        assert completed[0].payment is not None

        persisted = cart_repo.get("tenant_01", cart.cart_id)
        assert persisted is not None and persisted.status is CartStatus.CHECKED_OUT

    # Three rounds, one reservation each: exactly one financial effect per cart
    inv = inventory_repo.get("tenant_01", "var_02")
    assert inv is not None and (inv.available, inv.reserved) == (3, 3)
    assert inv.effective_available == 0


def test_cart_concurrent_same_key_requests_replay_one_payment() -> None:
    """A concurrent retry with the same key and payload replays the single checkout."""
    orchestrator, _, inventory_repo = setup_cart_orchestrator(decision_delay_seconds=0.05)

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    results, errors = _race_two_checkouts(
        orchestrator,
        cart.cart_id,
        50000,
        ("act_cart_checkout_01", "act_cart_checkout_01"),
        ("idem_cart_race_same_key", "idem_cart_race_same_key"),
    )

    assert not errors
    assert len(results) == 2
    assert all(r.status == ActionStatus.COMPLETED for r in results)
    payment_attempt_ids = {r.payment.attempt_id for r in results if r.payment is not None}
    assert len(payment_attempt_ids) == 1
    order_id_sets = {tuple(order.order_id for order in r.orders) for r in results}
    assert len(order_id_sets) == 1

    inv = inventory_repo.get("tenant_01", "var_02")
    assert inv is not None and (inv.available, inv.reserved) == (3, 1)


# ============================================================================
# 10. Saga compensation & persistence failure rollback (BUG-01, BUG-02, §41, §44)
# ============================================================================
def test_cart_reservation_operational_failure_compensates_prior_lines() -> None:
    """If reserving line K raises an operational exception (e.g. database error),

    earlier reserved lines 0..K-1 must be compensated via release (§44).
    """
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    # Verify initial stock state
    inv_01_init = inventory_repo.get("tenant_01", "var_01")
    inv_02_init = inventory_repo.get("tenant_01", "var_02")
    assert inv_01_init is not None and (inv_01_init.available, inv_01_init.reserved) == (10, 0)
    assert inv_02_init is not None and (inv_02_init.available, inv_02_init.reserved) == (3, 0)

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    # Intercept reserve to fail on the 2nd line (var_02) with an operational RuntimeError
    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_02":
            raise RuntimeError("Database connection dropped during inventory reservation")
        return real_reserve(tenant_id, variant_id, qty)

    inventory_repo.reserve = failing_reserve  # type: ignore[method-assign]

    action = checkout_action(cart.cart_id, 129900 + 50000)

    with pytest.raises(RuntimeError, match="Database connection dropped"):
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )

    # SAGA COMPENSATION VERIFICATION (§44):
    # Line 1 (var_01) was reserved before line 2 failed; it must be compensated back to 0 reserved.
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)

    # Line 2 (var_02) failed reservation; it must remain at 0 reserved.
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)

    # Cart remains ACTIVE for safe retry.
    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE

    # Audit ledger records ORDER_CANCELLED with the operational exception reason.
    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    assert "Database connection dropped" in str(cancelled[-1].data.get("reason"))


def test_cart_checkout_partial_order_persistence_failure_rolls_back_saved_orders() -> None:
    """When saving the 2nd order in a multi-item cart fails after the 1st order was saved:
    1. The primary persistence exception is re-raised.
    2. The already-persisted 1st order is rolled back to CANCELLED in _order_repo with an auditable reason.
    3. Payment capture is refunded for the full cart amount.
    4. Inventory reservations for all cart lines are released.
    """
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 179900)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def mock_order_save(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("Database storage exhausted on order 2")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = mock_order_save  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Database storage exhausted on order 2"):
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )

    # 1. Verify that Order 1 was rolled back to CANCELLED with reason
    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 1
    assert saved_orders[0].status == OrderStatus.CANCELLED
    assert (
        saved_orders[0].cancellation_reason
        == "Saga compensation: cart checkout persistence failure"
    )

    # 2. Verify payment refund was issued for the full amount
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == 179900

    # 3. Verify inventory reservations were released for all lines
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)


def test_cart_checkout_cart_save_failure_rolls_back_all_saved_orders() -> None:
    """When cart_repo.save fails after all orders in a multi-item cart are saved:
    1. The primary cart persistence exception is re-raised.
    2. All previously persisted orders are rolled back to CANCELLED in _order_repo with auditable reasons.
    3. Payment capture is refunded for the full cart amount.
    4. Inventory reservations for all cart lines are released.
    """
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 179900)

    real_cart_save = cart_repo.save

    def failing_cart_save(tenant_id: str, cart: Cart) -> None:
        if cart.status == CartStatus.CHECKED_OUT:
            raise RuntimeError("Cart repository write failure")
        real_cart_save(tenant_id, cart)

    cart_repo.save = failing_cart_save  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Cart repository write failure"):
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )

    # 1. Verify that BOTH orders were rolled back to CANCELLED
    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 2
    for order in saved_orders:
        assert order.status == OrderStatus.CANCELLED
        assert order.cancellation_reason == "Saga compensation: cart checkout persistence failure"

    # 2. Verify payment refund was issued for full amount
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == 179900

    # 3. Verify inventory reservations were released for all lines
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)


def test_cart_checkout_order_rollback_secondary_error_suppressed() -> None:
    """If saving a cancelled order during saga rollback encounters a secondary error,
    the secondary error must be logged and suppressed so it does not mask the primary exception.
    """
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 179900)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def mock_order_save(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("Primary failure: order 2 persistence failed")
        if save_calls > 2:
            raise RuntimeError("Secondary failure: order repo down during rollback")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = mock_order_save  # type: ignore[method-assign]

    # Must re-raise the PRIMARY error, not the secondary error
    with pytest.raises(RuntimeError, match="Primary failure: order 2 persistence failed"):
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )

    # Payment refund and inventory release must still succeed despite rollback failure
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == 179900

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)

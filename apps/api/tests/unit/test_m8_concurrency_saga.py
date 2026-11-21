"""Milestone M8 Unit Tests: Database Concurrency, TOCTOU & SAGA Compensation (§41, §42, §43, §49).

Covers all 7 features:
- Feature 31: Multi-Process Cart Checkout Concurrency Guard (VULN-03)
- Feature 32: Database Inventory Reservation Locking (VULN-02)
- Feature 33: Daily Spend Concurrency Pre-Allocation & Rollback (VULN-04)
- Feature 34: SAGA Compensation for Multi-Item Cart Orders (VULN-05)
- Feature 35: Audit Hash Chain Concurrency Guard (VULN-07)
- Feature 36: Database Outbox Event Claiming Locking (VULN-08)
- Feature 37: Product Search LIKE Wildcard DoS Mitigation (VULN-12)
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from test_cart_flow import checkout_action, setup_cart_orchestrator
from test_purchase_flow import setup_orchestrator

import agentpay.infrastructure.database.models  # noqa: F401
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryCartRepository,
    InMemoryInventoryRepository,
)
from agentpay.adapters.in_memory.payment_repository import (
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
    InMemoryRefundRepository,
)
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.cart import Cart, CartStatus
from agentpay.domain.commerce import Inventory, Product, ProductVariant
from agentpay.domain.errors import (
    InsufficientInventoryError,
)
from agentpay.domain.identity import AgentPermissions
from agentpay.domain.money import Money
from agentpay.domain.outbox import OutboxEvent
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)
from agentpay.domain.policy import PolicyDecision, SpendingLimits
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.audit_ledger import (
    SqlAlchemyAuditRepository,
)
from agentpay.infrastructure.database.repositories.cart_repository import (
    SqlAlchemyCartRepository,
)
from agentpay.infrastructure.database.repositories.commerce_repository import (
    SqlAlchemyInventoryRepository,
    SqlAlchemyProductRepository,
)
from agentpay.infrastructure.database.repositories.order_repository import (
    SqlAlchemyOrderRepository,
)
from agentpay.infrastructure.database.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    """Isolated, in-memory SQLite database factory for unit tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# ============================================================================
# Feature 31: Multi-Process Cart Checkout Concurrency Guard (VULN-03)
# ============================================================================


def test_in_memory_cart_mark_processing_atomic_transition() -> None:
    repo = InMemoryCartRepository()
    cart = Cart("cart_1", "tenant_1", "principal_1", "agent_1")
    repo.save("tenant_1", cart)

    # First transition must succeed
    assert repo.mark_processing("tenant_1", cart.cart_id) is True
    updated = repo.get("tenant_1", cart.cart_id)
    assert updated is not None
    assert updated.status is CartStatus.PROCESSING

    # Second transition must fail because status is already PROCESSING
    assert repo.mark_processing("tenant_1", cart.cart_id) is False

    # Mark active allows resetting on downstream failure
    assert repo.mark_active("tenant_1", cart.cart_id) is True
    updated2 = repo.get("tenant_1", cart.cart_id)
    assert updated2 is not None
    assert updated2.status is CartStatus.ACTIVE

    # Check out from PROCESSING is allowed
    checked_out = updated.mark_checked_out()
    assert checked_out.status is CartStatus.CHECKED_OUT


def test_sqlalchemy_cart_mark_processing_atomic_transition(
    session_factory: sessionmaker[Session],
) -> None:
    repo = SqlAlchemyCartRepository(session_factory)
    cart = Cart("cart_1", "tenant_1", "principal_1", "agent_1")
    repo.save("tenant_1", cart)

    # First transition must succeed
    assert repo.mark_processing("tenant_1", cart.cart_id) is True
    updated = repo.get("tenant_1", cart.cart_id)
    assert updated is not None
    assert updated.status is CartStatus.PROCESSING

    # Second transition must fail
    assert repo.mark_processing("tenant_1", cart.cart_id) is False

    # Reset to ACTIVE
    assert repo.mark_active("tenant_1", cart.cart_id) is True
    updated2 = repo.get("tenant_1", cart.cart_id)
    assert updated2 is not None
    assert updated2.status is CartStatus.ACTIVE

    # Cross-tenant transition cannot succeed
    assert repo.mark_processing("tenant_2", cart.cart_id) is False


def test_cart_checkout_concurrency_race_single_winner() -> None:
    """Concurrent checkout requests on the same active cart allow exactly 1 winner."""
    orchestrator, _cart_repo, _inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)

    results = []
    num_threads = 4
    barrier = threading.Barrier(num_threads)

    def worker(idx: int) -> None:
        barrier.wait()
        act = checkout_action(cart.cart_id, amount_minor=129900, action_id=f"act_race_{idx}")
        res = orchestrator.execute_cart_purchase(act, tenant_id="tenant_01", cart_id=cart.cart_id)
        results.append(res)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.status is ActionStatus.COMPLETED]
    failures = [r for r in results if r.status is ActionStatus.FAILED]
    assert len(successes) == 1
    assert len(failures) == num_threads - 1


# ============================================================================
# Feature 32: Database Inventory Reservation Locking (VULN-02)
# ============================================================================


def test_sqlalchemy_inventory_concurrent_reservations_no_oversell(
    session_factory: sessionmaker[Session],
) -> None:
    """10 concurrent threads attempting to reserve 1 unit from 5 available units.

    Exactly 5 must succeed and 5 must fail with InsufficientInventoryError.
    Final reserved count in DB must be exactly 5.
    """
    repo = SqlAlchemyInventoryRepository(session_factory)
    tenant_id = "tenant_inv_lock"
    variant_id = "var_limited"

    # Total 5 available
    inv = Inventory(variant_id=variant_id, tenant_id=tenant_id, available=5, reserved=0)
    repo.save(tenant_id, inv)

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    successes = 0
    failures = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal successes, failures
        barrier.wait()
        try:
            repo.reserve(tenant_id, variant_id, 1)
            with lock:
                successes += 1
        except InsufficientInventoryError:
            with lock:
                failures += 1

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert successes == 5
    assert failures == 5

    final_inv = repo.get(tenant_id, variant_id)
    assert final_inv is not None
    assert final_inv.available == 5
    assert final_inv.reserved == 5

    # Release 2 units
    repo.release(tenant_id, variant_id, 2)
    after_release = repo.get(tenant_id, variant_id)
    assert after_release is not None
    assert after_release.reserved == 3


# ============================================================================
# Feature 33: Daily Spend Concurrency Pre-Allocation & Rollback (VULN-04)
# ============================================================================


def test_daily_spend_pre_allocation_prevents_concurrent_limit_bypass() -> None:
    """Two concurrent requests of ₹600 each when daily limit is ₹1,000.

    Without pre-allocation, both could pass policy concurrently (spending ₹1,200).
    With atomic pre-allocation, exactly one succeeds and one is rejected.
    """
    tenant_id = "tenant_spend"
    orch, _, _, perms_repo, _, prod_repo, var_repo, inv_repo, _, _, _ = setup_orchestrator(
        tenant_id
    )

    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=100000,
            daily_minor=100000,  # ₹1,000 max daily spend
            requires_approval_above_minor=200000,
        ),
        allowed_categories=("electronics", "computers"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save(tenant_id, perms)

    prod = Product(
        product_id="prod_spend",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Phone",
        category="electronics",
        description="Good phone",
    )
    var = ProductVariant(
        variant_id="var_spend",
        product_id="prod_spend",
        sku="PH-01",
        name="Standard",
        price=Money(60000, "INR"),  # ₹600
    )
    prod_repo.save(tenant_id, prod)
    var_repo.save(tenant_id, var)
    inv_repo.save(tenant_id, Inventory("var_spend", tenant_id, available=10, reserved=0))

    def make_action(idx: int) -> ActionRequest:
        return ActionRequest(
            action_id=f"act_spend_{idx}_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product_variant", id="var_spend"),
            requested_at=datetime.now(UTC),
            amount=Money(60000, "INR"),
            intent=Intent(purpose="work item"),
            authorization_mode=AuthorizationMode.AUTONOMOUS,
        )

    results = []
    num_threads = 2
    barrier = threading.Barrier(num_threads)

    def worker(idx: int) -> None:
        act = make_action(idx)
        barrier.wait()
        res = orch.execute_purchase(
            action=act, tenant_id=tenant_id, variant_id="var_spend", quantity=1
        )
        results.append(res)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    completed = [r for r in results if r.status is ActionStatus.COMPLETED]
    denied = [
        r for r in results if r.status is ActionStatus.FAILED or r.decision is PolicyDecision.DENY
    ]
    assert len(completed) == 1
    assert len(denied) == 1


def test_daily_spend_rollback_on_payment_decline() -> None:
    """When payment is declined, pre-allocated daily spend is rolled back.

    The subsequent purchase within budget must succeed.
    """
    tenant_id = "tenant_rollback"
    orch, _, _, perms_repo, _, prod_repo, var_repo, inv_repo, provider, _, _ = setup_orchestrator(
        tenant_id, simulate_decline=True
    )

    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=100000,
            daily_minor=100000,  # ₹1,000 daily limit
            requires_approval_above_minor=200000,
        ),
        allowed_categories=("electronics", "computers"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save(tenant_id, perms)

    prod = Product("prod_rb", tenant_id, "merch_01", "Item", "computers", "Desc")
    var = ProductVariant("var_rb", "prod_rb", "SKU-RB", "Item", Money(80000, "INR"))  # ₹800
    prod_repo.save(tenant_id, prod)
    var_repo.save(tenant_id, var)
    inv_repo.save(tenant_id, Inventory("var_rb", tenant_id, available=5, reserved=0))

    action1 = ActionRequest(
        action_id=f"act_rb_1_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product_variant", id="var_rb"),
        requested_at=datetime.now(UTC),
        amount=Money(80000, "INR"),
        intent=Intent(purpose="work item"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    # First purchase: provider simulates decline
    res1 = orch.execute_purchase(
        action=action1, tenant_id=tenant_id, variant_id="var_rb", quantity=1
    )
    assert res1.status is ActionStatus.FAILED

    # Spend pre-allocation was rolled back, so agent still has ₹1,000 available
    provider.simulate_decline = False
    action2 = ActionRequest(
        action_id=f"act_rb_2_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product_variant", id="var_rb"),
        requested_at=datetime.now(UTC),
        amount=Money(80000, "INR"),
        intent=Intent(purpose="work item"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )
    res2 = orch.execute_purchase(
        action=action2, tenant_id=tenant_id, variant_id="var_rb", quantity=1
    )
    assert res2.status is ActionStatus.COMPLETED


def test_daily_spend_released_on_insufficient_inventory_error() -> None:
    """When single-item purchase fails due to InsufficientInventoryError (TOCTOU),

    pre-allocated daily spend must be rolled back to 0.
    """
    tenant_id = "tenant_inv_rollback"
    orch, _, _, perms_repo, _, prod_repo, var_repo, inv_repo, _, _, _ = setup_orchestrator(
        tenant_id
    )

    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=100000,
            daily_minor=100000,  # ₹1,000 daily limit
            requires_approval_above_minor=200000,
        ),
        allowed_categories=("electronics", "computers"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save(tenant_id, perms)

    prod = Product("prod_inv_rb", tenant_id, "merch_01", "Item", "computers", "Desc")
    var = ProductVariant(
        "var_inv_rb", "prod_inv_rb", "SKU-INV-RB", "Item", Money(80000, "INR")
    )  # ₹800
    prod_repo.save(tenant_id, prod)
    var_repo.save(tenant_id, var)
    # Available = 5 so evidence gathering passes, but simulate concurrent TOCTOU exhaustion on reserve
    inv_repo.save(tenant_id, Inventory("var_inv_rb", tenant_id, available=5, reserved=0))

    def failing_reserve(t_id: str, v_id: str, q: int) -> Inventory:
        raise InsufficientInventoryError("Concurrent inventory taken")

    orig_reserve = inv_repo.reserve
    inv_repo.reserve = failing_reserve  # type: ignore[assignment,method-assign]

    action = ActionRequest(
        action_id=f"act_inv_rb_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product_variant", id="var_inv_rb"),
        requested_at=datetime.now(UTC),
        amount=Money(80000, "INR"),
        intent=Intent(purpose="work item"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    res = orch.execute_purchase(
        action=action, tenant_id=tenant_id, variant_id="var_inv_rb", quantity=1
    )
    assert res.status is ActionStatus.FAILED
    assert "Concurrent inventory taken" in (res.rejection_reason or "")

    # Pre-allocated daily spend must be rolled back
    day = datetime.now(UTC).date().isoformat()
    key = (tenant_id, "agent_01", day)
    assert orch._daily_spend.get(key, 0) == 0

    # Restore reserve method: subsequent purchase must succeed within daily budget
    inv_repo.reserve = orig_reserve  # type: ignore[method-assign]
    action2 = ActionRequest(
        action_id=f"act_inv_rb_2_{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product_variant", id="var_inv_rb"),
        requested_at=datetime.now(UTC),
        amount=Money(80000, "INR"),
        intent=Intent(purpose="work item"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )
    res2 = orch.execute_purchase(
        action=action2, tenant_id=tenant_id, variant_id="var_inv_rb", quantity=1
    )
    assert res2.status is ActionStatus.COMPLETED
    assert orch._daily_spend.get(key, 0) == 80000


# ============================================================================
# Feature 34: SAGA Compensation for Multi-Item Cart Orders (VULN-05)
# ============================================================================


def test_order_repository_find_all_by_payment_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    mem_repo = InMemoryOrderRepository()
    sql_repo = SqlAlchemyOrderRepository(session_factory)

    for repo in (mem_repo, sql_repo):
        tenant_id = "tenant_saga_orders"
        attempt_id = f"att_{uuid.uuid4().hex[:10]}"

        orders = [
            Order(
                order_id=f"ord_{i}_{uuid.uuid4().hex[:8]}",
                tenant_id=tenant_id,
                merchant_id="merch_1",
                buyer_id="user_1",
                agent_id="agent_1",
                variant_id=f"var_{i}",
                quantity=i,
                total_amount=Money(1000 * i, "INR"),
                status=OrderStatus.PENDING_PAYMENT,
                payment_attempt_id=attempt_id,
            )
            for i in range(1, 4)
        ]
        for o in orders:
            repo.save(tenant_id, o)

        found = repo.find_all_by_payment_attempt(tenant_id, attempt_id)
        assert len(found) == 3
        found_ids = {o.order_id for o in found}
        assert found_ids == {o.order_id for o in orders}

        assert repo.find_all_by_payment_attempt("other_tenant", attempt_id) == []


def test_saga_webhook_reconciliation_multi_item_cart_failure() -> None:
    """When payment.failed webhook is received for a multi-item cart purchase,

    ALL orders for that payment attempt must be cancelled, and inventory
    for each line item must be restocked/released.
    """
    order_repo = InMemoryOrderRepository()
    payment_repo = InMemoryPaymentRepository()
    inv_repo = InMemoryInventoryRepository()
    mock_provider = MockPaymentProvider()

    tenant_id = "tenant_saga_reconcile"
    attempt_id = "att_cart_saga_01"

    payment_repo.save(
        tenant_id,
        PaymentAttempt(
            attempt_id=attempt_id,
            intent_id="intent_cart_01",
            tenant_id=tenant_id,
            amount=Money(3000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_cart_01",
            provider="mock",
        ),
    )

    order1 = Order(
        "ord_item_1",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_1",
        1,
        Money(1000, "INR"),
        OrderStatus.PENDING_PAYMENT,
        attempt_id,
    )
    order2 = Order(
        "ord_item_2",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_2",
        2,
        Money(2000, "INR"),
        OrderStatus.PENDING_PAYMENT,
        attempt_id,
    )
    order_repo.save(tenant_id, order1)
    order_repo.save(tenant_id, order2)

    inv_repo.save(tenant_id, Inventory("var_1", tenant_id, available=10, reserved=1))
    inv_repo.save(tenant_id, Inventory("var_2", tenant_id, available=10, reserved=2))

    reconciler = PaymentReconciler(
        payment_provider=mock_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        inventory_repo=inv_repo,
    )

    result = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.failed",
        payload={"attempt_id": attempt_id, "provider": "mock", "reason": "Insufficient funds"},
    )
    assert result["status"] == "PROCESSED"

    updated_orders = order_repo.find_all_by_payment_attempt(tenant_id, attempt_id)
    assert len(updated_orders) == 2
    for ord_item in updated_orders:
        assert ord_item.status is OrderStatus.CANCELLED

    inv1 = inv_repo.get(tenant_id, "var_1")
    assert inv1 is not None and inv1.reserved == 0
    inv2 = inv_repo.get(tenant_id, "var_2")
    assert inv2 is not None and inv2.reserved == 0


def test_saga_webhook_reconciliation_multi_item_cart_refund() -> None:
    """When payment.refunded webhook is received for a multi-item cart,

    ALL orders must transition to REFUNDED and reserved inventory released.
    """
    order_repo = InMemoryOrderRepository()
    payment_repo = InMemoryPaymentRepository()
    refund_repo = InMemoryRefundRepository()
    inv_repo = InMemoryInventoryRepository()
    mock_provider = MockPaymentProvider()

    tenant_id = "tenant_saga_refund"
    attempt_id = "att_cart_refund_01"

    payment_repo.save(
        tenant_id,
        PaymentAttempt(
            attempt_id=attempt_id,
            intent_id="intent_cart_ref",
            tenant_id=tenant_id,
            amount=Money(4000, "INR"),
            status=PaymentStatus.CAPTURED,
            idempotency_key="idem_cart_ref",
            provider="mock",
        ),
    )

    order1 = Order(
        "ord_ref_1",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_1",
        2,
        Money(2000, "INR"),
        OrderStatus.CONFIRMED,
        attempt_id,
    )
    order2 = Order(
        "ord_ref_2",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_2",
        2,
        Money(2000, "INR"),
        OrderStatus.CONFIRMED,
        attempt_id,
    )
    order_repo.save(tenant_id, order1)
    order_repo.save(tenant_id, order2)

    inv_repo.save(tenant_id, Inventory("var_1", tenant_id, available=10, reserved=2))
    inv_repo.save(tenant_id, Inventory("var_2", tenant_id, available=10, reserved=2))

    reconciler = PaymentReconciler(
        payment_provider=mock_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        refund_repo=refund_repo,
        inventory_repo=inv_repo,
    )

    result = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.refunded",
        payload={
            "attempt_id": attempt_id,
            "provider": "mock",
            "amount_minor": 4000,
            "currency": "INR",
        },
    )
    assert result["status"] == "PROCESSED"

    updated_orders = order_repo.find_all_by_payment_attempt(tenant_id, attempt_id)
    assert len(updated_orders) == 2
    for ord_item in updated_orders:
        assert ord_item.status is OrderStatus.REFUNDED

    inv1 = inv_repo.get(tenant_id, "var_1")
    assert inv1 is not None and inv1.reserved == 0
    inv2 = inv_repo.get(tenant_id, "var_2")
    assert inv2 is not None and inv2.reserved == 0


def test_cumulative_partial_refunds_trigger_full_saga_compensation() -> None:
    """Cumulative partial refunds totaling 100% of payment amount must transition

    attempt to REFUNDED, transition all cart orders to REFUNDED, and release
    all reserved inventory (SAGA compensation).
    """
    order_repo = InMemoryOrderRepository()
    payment_repo = InMemoryPaymentRepository()
    refund_repo = InMemoryRefundRepository()
    inv_repo = InMemoryInventoryRepository()
    mock_provider = MockPaymentProvider()

    tenant_id = "tenant_cum_refund"
    attempt_id = "att_cum_refund_01"

    # Total amount = ₹600 (60,000 minor units)
    payment_repo.save(
        tenant_id,
        PaymentAttempt(
            attempt_id=attempt_id,
            intent_id="intent_cum_ref",
            tenant_id=tenant_id,
            amount=Money(60000, "INR"),
            status=PaymentStatus.CAPTURED,
            idempotency_key="idem_cum_ref",
            provider="mock",
        ),
    )

    order1 = Order(
        "ord_cum_1",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_1",
        1,
        Money(20000, "INR"),
        OrderStatus.CONFIRMED,
        attempt_id,
    )
    order2 = Order(
        "ord_cum_2",
        tenant_id,
        "m1",
        "u1",
        "a1",
        "var_2",
        2,
        Money(40000, "INR"),
        OrderStatus.CONFIRMED,
        attempt_id,
    )
    order_repo.save(tenant_id, order1)
    order_repo.save(tenant_id, order2)

    inv_repo.save(tenant_id, Inventory("var_1", tenant_id, available=10, reserved=1))
    inv_repo.save(tenant_id, Inventory("var_2", tenant_id, available=10, reserved=2))

    reconciler = PaymentReconciler(
        payment_provider=mock_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        refund_repo=refund_repo,
        inventory_repo=inv_repo,
    )

    # 1. First partial refund: ₹200 (20,000 minor units)
    res1 = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.refunded",
        payload={
            "attempt_id": attempt_id,
            "provider": "mock",
            "amount_minor": 20000,
            "currency": "INR",
        },
    )
    assert res1["status"] == "PROCESSED"
    attempt_after_1 = payment_repo.get(tenant_id, attempt_id)
    assert attempt_after_1 is not None
    assert attempt_after_1.status is PaymentStatus.PARTIALLY_REFUNDED

    # Orders must remain CONFIRMED, inventory reserved
    orders_1 = order_repo.find_all_by_payment_attempt(tenant_id, attempt_id)
    for ord_item in orders_1:
        assert ord_item.status is OrderStatus.CONFIRMED
    inv1_mid = inv_repo.get(tenant_id, "var_1")
    assert inv1_mid is not None and inv1_mid.reserved == 1
    inv2_mid = inv_repo.get(tenant_id, "var_2")
    assert inv2_mid is not None and inv2_mid.reserved == 2

    # 2. Second partial refund: ₹400 (40,000 minor units) -> Cumulative 60,000 = 100%
    res2 = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.refunded",
        payload={
            "attempt_id": attempt_id,
            "provider": "mock",
            "amount_minor": 40000,
            "currency": "INR",
        },
    )
    assert res2["status"] == "PROCESSED"
    attempt_after_2 = payment_repo.get(tenant_id, attempt_id)
    assert attempt_after_2 is not None
    assert attempt_after_2.status is PaymentStatus.REFUNDED

    # All cart orders must now transition to REFUNDED and reserved inventory restocked to 0
    orders_2 = order_repo.find_all_by_payment_attempt(tenant_id, attempt_id)
    assert len(orders_2) == 2
    for ord_item in orders_2:
        assert ord_item.status is OrderStatus.REFUNDED

    inv1_end = inv_repo.get(tenant_id, "var_1")
    assert inv1_end is not None and inv1_end.reserved == 0
    inv2_end = inv_repo.get(tenant_id, "var_2")
    assert inv2_end is not None and inv2_end.reserved == 0


def test_reconcile_webhook_default_trace_id_audit_logging() -> None:
    """When audit ledger is wired, calling reconcile_webhook_event with default trace_id

    (or trace_id="") must not crash with InvalidAuditEventError.
    """
    order_repo = InMemoryOrderRepository()
    payment_repo = InMemoryPaymentRepository()
    mock_provider = MockPaymentProvider()
    audit_repo = InMemoryAuditLedger()

    tenant_id = "tenant_audit_trace"
    attempt_id = "att_trace_01"

    payment_repo.save(
        tenant_id,
        PaymentAttempt(
            attempt_id=attempt_id,
            intent_id="intent_trace_01",
            tenant_id=tenant_id,
            amount=Money(5000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_trace_01",
            provider="mock",
        ),
    )

    reconciler = PaymentReconciler(
        payment_provider=mock_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        audit_ledger=audit_repo,
    )

    # 1. Call without specifying trace_id parameter (default argument None)
    res1 = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.failed",
        payload={"attempt_id": attempt_id, "provider": "mock", "reason": "Declined"},
    )
    assert res1["status"] == "PROCESSED"

    # 2. Call with explicit empty trace_id=""
    attempt2_id = "att_trace_02"
    payment_repo.save(
        tenant_id,
        PaymentAttempt(
            attempt_id=attempt2_id,
            intent_id="intent_trace_02",
            tenant_id=tenant_id,
            amount=Money(5000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_trace_02",
            provider="mock",
        ),
    )
    res2 = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type="payment.captured",
        payload={
            "attempt_id": attempt2_id,
            "provider": "mock",
            "amount_minor": 5000,
            "currency": "INR",
        },
        trace_id="",
    )
    assert res2["status"] == "PROCESSED"

    # Verify audit events were recorded without errors and trace_id is None
    events = audit_repo.get_events(tenant_id)
    assert len(events) >= 2
    for event in events:
        assert event.trace_id is None


# ============================================================================
# Feature 35: Audit Hash Chain Concurrency Guard (VULN-07)
# ============================================================================


def test_sqlalchemy_audit_ledger_concurrent_writes_preserve_hash_chain(
    session_factory: sessionmaker[Session],
) -> None:
    """Concurrent threads writing to the same tenant's audit ledger must preserve

    the cryptographic SHA-256 hash chain without chain forks.
    """
    repo = SqlAlchemyAuditRepository(session_factory)
    tenant_id = "tenant_audit_chain_test"

    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def worker(i: int) -> None:
        barrier.wait()
        repo.record_event(
            event_type=AuditEventType.ACTION_REQUESTED,
            action_id=f"act_concur_{i}",
            tenant_id=tenant_id,
            actor="test_worker",
            data={"index": i},
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = repo.get_events(tenant_id)
    assert len(events) == num_threads

    assert repo.verify_chain(tenant_id) is True


# ============================================================================
# Feature 36: Database Outbox Event Claiming Locking (VULN-08)
# ============================================================================


def test_sqlalchemy_outbox_concurrent_claiming_prevents_duplicate_claims(
    session_factory: sessionmaker[Session],
) -> None:
    """Multiple concurrent workers calling claim_pending must not claim

    the same outbox events simultaneously.
    """
    repo = SqlAlchemyOutboxRepository(session_factory)
    tenant_id = "tenant_outbox_conc"

    events = [
        OutboxEvent(
            tenant_id=tenant_id,
            event_type="test.event",
            aggregate_type="order",
            aggregate_id=f"ord_{i}",
            payload={"index": i},
            event_id=f"evt_{i}_{uuid.uuid4().hex[:8]}",
        )
        for i in range(12)
    ]
    for e in events:
        repo.save(tenant_id, e)

    num_workers = 4
    barrier = threading.Barrier(num_workers)
    all_claimed_ids: list[str] = []
    lock = threading.Lock()

    def worker(worker_idx: int) -> None:
        barrier.wait()
        claimed = repo.claim_pending(worker_id=f"worker_{worker_idx}", limit=3)
        with lock:
            all_claimed_ids.extend([e.event_id for e in claimed])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(all_claimed_ids) == len(set(all_claimed_ids)), (
        f"Duplicate event claims detected! Total claimed: {len(all_claimed_ids)}, unique: {len(set(all_claimed_ids))}"
    )


# ============================================================================
# Feature 37: Product Search LIKE Wildcard DoS Mitigation (VULN-12)
# ============================================================================


def test_sqlalchemy_product_search_wildcard_escaping(
    session_factory: sessionmaker[Session],
) -> None:
    """Product search must escape '%', '_', and '\\' characters in user query

    so they match literal characters instead of triggering full-table wildcard scans.
    """
    repo = SqlAlchemyProductRepository(session_factory)
    tenant_id = "tenant_search_test"

    prod1 = Product(
        "p1", tenant_id, "m1", "100% Cotton Shirt", "apparel", "Made of 100% pure organic cotton"
    )
    prod2 = Product("p2", tenant_id, "m1", "Cotton Shirt", "apparel", "Regular cotton blend")
    prod3 = Product(
        "p3", tenant_id, "m1", "Item_1 Product", "electronics", "Model Item_1 with underscore"
    )
    prod4 = Product(
        "p4", tenant_id, "m1", "ItemX1 Product", "electronics", "Model ItemX1 with letter X"
    )

    for p in (prod1, prod2, prod3, prod4):
        repo.save(tenant_id, p)

    results_pct = repo.search(tenant_id, "100%")
    matched_ids_pct = {p.product_id for p in results_pct}
    assert matched_ids_pct == {"p1"}, (
        f"Expected only p1 for '100%' search, but got {matched_ids_pct}"
    )

    results_under = repo.search(tenant_id, "Item_1")
    matched_ids_under = {p.product_id for p in results_under}
    assert matched_ids_under == {"p3"}, (
        f"Expected only p3 for 'Item_1' search, but got {matched_ids_under}"
    )

    results_slash = repo.search(tenant_id, "\\")
    assert isinstance(results_slash, list)

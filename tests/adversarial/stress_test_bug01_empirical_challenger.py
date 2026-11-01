"""Empirical Challenger Stress Harness for BUG-01 (ADR-0006, §41, §44).

Empirically tests and challenges Multi-Item Cart Saga Order Rollback:
1. Order persistence failure on order N rolls back orders 0..N-1 to OrderStatus.CANCELLED
   with valid non-empty cancellation reason, refunds payment capture, and releases inventory holds.
2. Order persistence failure on larger carts (5-item cart) rolls back all prior orders.
3. Boundary condition: Order persistence failure on order 0 safely rolls back with 0 prior orders,
   issuing refund and releasing all inventory holds without crashing.
4. Cart persistence failure after saving all orders rolls back ALL confirmed orders to
   OrderStatus.CANCELLED with non-empty cancellation reason, refunds payment, and releases holds.
5. Secondary exceptions during order rollback do NOT mask primary order save exception and do
   NOT abort rollback of subsequent orders.
6. Secondary exceptions during order rollback after cart save failure do NOT mask primary cart
   save exception.
7. Payment refund failure during saga compensation does NOT abort order rollback or inventory
   release, and does NOT mask primary exception.
8. Domain Order.cancel() enforces idempotency, non-empty reason, and blocks cancelling refunded orders.
9. End-to-end relational database (SQLAlchemy / SQLite) cart saga rollback persists CANCELLED
   status and non-empty cancellation reason to the database table.
10. Multi-tenant isolation is strictly preserved during saga order rollback.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))
sys.path.insert(0, str(Path("apps/worker/src").resolve()))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_cart_flow import (  # type: ignore[import-not-found]
    checkout_action,
    setup_cart_orchestrator,
)

from agentpay.adapters.in_memory.payment_repository import InMemoryOrderRepository
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.application.use_cases.purchase_flow import PurchaseOrchestrator
from agentpay.domain.audit import AuditEventType
from agentpay.domain.cart import Cart, CartStatus
from agentpay.domain.commerce import Inventory, ProductVariant
from agentpay.domain.errors import CrossTenantAccessError, InvalidPaymentError
from agentpay.domain.identity import AgentPermissions
from agentpay.domain.money import Money
from agentpay.domain.payments import Order, OrderStatus
from agentpay.domain.policy import SpendingLimits
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.models.order import OrderModel
from agentpay.infrastructure.database.repositories.order_repository import (
    SqlAlchemyOrderRepository,
)


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def setup_autonomous_cart_orchestrator(
    requires_approval_above_minor: int = 10_000_000,
) -> tuple[PurchaseOrchestrator, Any, Any]:
    """Helper to seed orchestrator with generous autonomous limit for testing order pipeline."""
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()
    assert orchestrator._permissions_repo is not None
    perms = orchestrator._permissions_repo.get("tenant_01", "agent_01")
    assert perms is not None
    orchestrator._permissions_repo.save(
        "tenant_01",
        AgentPermissions(
            agent_id="agent_01",
            tenant_id="tenant_01",
            capabilities=perms.capabilities,
            limits=SpendingLimits(
                per_transaction_minor=20_000_000,
                daily_minor=50_000_000,
                requires_approval_above_minor=requires_approval_above_minor,
            ),
            allowed_categories=perms.allowed_categories,
            requires_approval_above_minor=requires_approval_above_minor,
        ),
    )
    return orchestrator, cart_repo, inventory_repo


def probe_1_order_save_failure_rolls_back_prior_orders() -> bool:
    """Probe 1: Order save failure at order N (index 1 in 3-item cart).

    Verifies orders 0..N-1 are cancelled with valid reason, payment refunded, holds released.
    """
    log("[->] Probe 1: Partial order save failure (index 1) rolls back order 0...")
    orchestrator, _, inventory_repo = setup_autonomous_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=1)

    total_minor = 129900 + 50000 + 100000
    action = checkout_action(cart.cart_id, total_minor)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def failing_order_save(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("Database storage exhausted on order 2")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = failing_order_save  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "Database storage exhausted on order 2" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # 1. Order 0 must be in _order_repo with status CANCELLED and valid reason
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 1, f"Expected 1 saved order in repo, got {len(saved_orders)}"
    assert saved_orders[0].status == OrderStatus.CANCELLED
    assert bool(saved_orders[0].cancellation_reason and saved_orders[0].cancellation_reason.strip())
    assert (
        saved_orders[0].cancellation_reason
        == "Saga compensation: cart checkout persistence failure"
    )

    # 2. Payment refund must be issued for full cart amount
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1, f"Expected 1 refund, got {len(refunds)}"
    assert refunds[0].amount.minor_units == total_minor
    assert refunds[0].reason == "Cart order persistence failure compensation"

    # 3. Inventory reservations for all 3 lines must be released
    for var_id in ("var_01", "var_02", "var_04"):
        inv = inventory_repo.get("tenant_01", var_id)
        assert inv is not None and inv.reserved == 0, f"Inventory hold not released for {var_id}"

    # 4. Audit ledger has ORDER_CANCELLED event
    assert orchestrator._audit_ledger is not None
    events = orchestrator._audit_ledger.get_events("tenant_01")
    cancel_events = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancel_events) >= 1
    log("    [+] Probe 1 PASSED: Order 0 cancelled with reason, payment refunded, stock released.")
    return True


def probe_2_order_save_failure_large_cart() -> bool:
    """Probe 2: Order save failure at index 3 in a 5-item cart.

    Verifies orders 0, 1, 2 are all cancelled with valid reason.
    """
    log("[->] Probe 2: Order save failure in 5-item cart rolls back all 3 prior orders...")
    orchestrator, _, inventory_repo = setup_autonomous_cart_orchestrator()

    # Add 2 more variants to catalog
    variant_repo = orchestrator._variant_repo
    assert variant_repo is not None
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_05",
            product_id="prod_01",
            sku="SKU-05",
            name="Variant 5",
            price=Money(20000, "INR"),
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_06",
            product_id="prod_01",
            sku="SKU-06",
            name="Variant 6",
            price=Money(30000, "INR"),
        ),
    )
    inventory_repo.save("tenant_01", Inventory("var_05", "tenant_01", available=10, reserved=0))
    inventory_repo.save("tenant_01", Inventory("var_06", "tenant_01", available=10, reserved=0))

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_05", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_06", quantity=1)

    total_minor = 129900 + 50000 + 100000 + 20000 + 30000
    action = checkout_action(cart.cart_id, total_minor)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def failing_on_order_4(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 4:
            raise RuntimeError("Failure on order 4 save")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = failing_on_order_4  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "Failure on order 4 save" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # Verify orders 0, 1, 2 are in repo and all CANCELLED
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 3, f"Expected 3 saved orders in repo, got {len(saved_orders)}"
    for o in saved_orders:
        assert o.status == OrderStatus.CANCELLED
        assert bool(o.cancellation_reason and o.cancellation_reason.strip())
        assert o.cancellation_reason == "Saga compensation: cart checkout persistence failure"

    # Verify refund for full total
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == total_minor

    # Verify all 5 inventory holds released
    for var_id in ("var_01", "var_02", "var_04", "var_05", "var_06"):
        inv = inventory_repo.get("tenant_01", var_id)
        assert inv is not None and inv.reserved == 0

    log("    [+] Probe 2 PASSED: All 3 prior orders in 5-item cart rolled back to CANCELLED.")
    return True


def probe_3_order_save_failure_on_first_order() -> bool:
    """Probe 3: Boundary condition: Order save failure on order 0.

    Verifies empty orders list is handled safely without crash, refund issued, stock released.
    """
    log("[->] Probe 3: Boundary condition: Order save failure on order 0...")
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 129900 + 50000)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)

    def fail_immediately(tenant_id: str, order: Order) -> None:
        raise RuntimeError("Failure on order 0 save")

    orchestrator._order_repo.save = fail_immediately  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "Failure on order 0 save" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # Repo must have 0 saved orders
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 0

    # Payment refund must be issued
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == 129900 + 50000

    # Inventory holds released
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and inv_01.reserved == 0
    assert inv_02 is not None and inv_02.reserved == 0

    log("    [+] Probe 3 PASSED: Boundary condition order 0 handled cleanly.")
    return True


def probe_4_cart_save_failure_rolls_back_all_orders() -> bool:
    """Probe 4: Cart persistence failure after saving all orders.

    Verifies ALL orders in repo are rolled back to CANCELLED with valid non-empty reason.
    """
    log("[->] Probe 4: Cart save failure after all orders saved rolls back all orders...")
    orchestrator, cart_repo, inventory_repo = setup_autonomous_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=2)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=1)

    total_minor = 129900 + (2 * 50000) + 100000
    action = checkout_action(cart.cart_id, total_minor)

    real_cart_save = cart_repo.save

    def failing_cart_save(tenant_id: str, c: Cart) -> None:
        if c.status == CartStatus.CHECKED_OUT:
            raise RuntimeError("Database connection lost during cart status update")
        real_cart_save(tenant_id, c)

    cart_repo.save = failing_cart_save

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "Database connection lost during cart status update" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # 1. All 3 orders must be CANCELLED in repo with non-empty cancellation reason
    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 3, f"Expected 3 orders, got {len(saved_orders)}"
    for order in saved_orders:
        assert order.status == OrderStatus.CANCELLED
        assert bool(order.cancellation_reason and order.cancellation_reason.strip())
        assert order.cancellation_reason == "Saga compensation: cart checkout persistence failure"

    # 2. Payment refund must be issued for full amount
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == total_minor
    assert refunds[0].reason == "Cart checkout persistence failure compensation"

    # 3. Inventory reservations released
    for var_id in ("var_01", "var_02", "var_04"):
        inv = inventory_repo.get("tenant_01", var_id)
        assert inv is not None and inv.reserved == 0

    log("    [+] Probe 4 PASSED: All 3 orders rolled back to CANCELLED on cart save failure.")
    return True


def probe_5_secondary_error_masking_prevention_on_order_save() -> bool:
    """Probe 5: Secondary error during order rollback does NOT mask primary exception.

    Also proves remaining orders are still cancelled despite an error on one.
    """
    log("[->] Probe 5: Secondary error during rollback does not mask primary error...")
    orchestrator, _, inventory_repo = setup_autonomous_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=1)

    total_minor = 129900 + 50000 + 100000
    action = checkout_action(cart.cart_id, total_minor)

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def faulty_order_save(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        # Order 0: save_calls=1 (ok)
        # Order 1: save_calls=2 (ok)
        # Order 2: save_calls=3 -> PRIMARY FAILURE
        if save_calls == 3:
            raise RuntimeError("PRIMARY: Order 2 persistence failed")
        # Rollback order 0: save_calls=4 -> SECONDARY FAILURE
        if save_calls == 4:
            raise ConnectionError("SECONDARY: Order repository socket reset during rollback")
        # Rollback order 1: save_calls=5 -> Should succeed!
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = faulty_order_save  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "PRIMARY: Order 2 persistence failed" in str(exc)
        # Verify secondary error was NOT raised
        assert "SECONDARY" not in str(exc)

    assert caught, "Primary RuntimeError was not raised!"

    # Rollback loop did not abort on order 0 failure: order 1 was successfully cancelled!
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    order_1 = next((o for o in saved_orders if o.variant_id == "var_02"), None)
    assert order_1 is not None, "Order 1 was not persisted!"
    assert order_1.status == OrderStatus.CANCELLED, "Order 1 was not cancelled after order 0 error!"

    # Payment refund and inventory release were NOT aborted by secondary error!
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == total_minor

    for var_id in ("var_01", "var_02", "var_04"):
        inv = inventory_repo.get("tenant_01", var_id)
        assert inv is not None and inv.reserved == 0

    log("    [+] Probe 5 PASSED: Primary error preserved, subsequent orders cancelled.")
    return True


def probe_6_secondary_error_masking_prevention_on_cart_save() -> bool:
    """Probe 6: Secondary error during order rollback after cart save failure.

    Verifies primary cart error is re-raised and other orders are still processed.
    """
    log("[->] Probe 6: Secondary error during order rollback after cart save failure...")
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    total_minor = 129900 + 50000
    action = checkout_action(cart.cart_id, total_minor)

    real_cart_save = cart_repo.save

    def failing_cart_save(tenant_id: str, c: Cart) -> None:
        if c.status == CartStatus.CHECKED_OUT:
            raise TimeoutError("PRIMARY: Cart save lock acquisition timeout")
        real_cart_save(tenant_id, c)

    cart_repo.save = failing_cart_save

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    order_save_calls = 0

    def faulty_order_save(tenant_id: str, order: Order) -> None:
        nonlocal order_save_calls
        order_save_calls += 1
        # Calls 1, 2: initial saves
        # Call 3: rollback order 0 -> SECONDARY ERROR
        if order_save_calls == 3:
            raise OSError("SECONDARY: Disk full during rollback of order 0")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = faulty_order_save  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except TimeoutError as exc:
        caught = True
        assert "PRIMARY: Cart save lock acquisition timeout" in str(exc)

    assert caught, "Expected TimeoutError was not raised!"

    # Order 1 was still rolled back despite order 0 error
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    order_1 = next((o for o in saved_orders if o.variant_id == "var_02"), None)
    assert order_1 is not None
    assert order_1.status == OrderStatus.CANCELLED

    # Payment refund and inventory holds released
    assert isinstance(orchestrator._payment_provider, MockPaymentProvider)
    refunds = list(orchestrator._payment_provider._refunds.values())
    assert len(refunds) == 1
    assert refunds[0].amount.minor_units == total_minor

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and inv_01.reserved == 0
    assert inv_02 is not None and inv_02.reserved == 0

    log("    [+] Probe 6 PASSED: Primary cart save error preserved without masking.")
    return True


def probe_7_refund_failure_does_not_abort_rollback() -> bool:
    """Probe 7: Payment refund failure during saga compensation.

    Verifies orders are STILL rolled back and inventory is STILL released.
    """
    log("[->] Probe 7: Payment refund failure does not stop order rollback or inventory release...")
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 129900 + 50000)

    # Make payment refund fail
    def failing_refund(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("SECONDARY: Payment gateway down during refund")

    assert orchestrator._payment_provider is not None
    orchestrator._payment_provider.refund = failing_refund

    assert isinstance(orchestrator._order_repo, InMemoryOrderRepository)
    real_order_save = orchestrator._order_repo.save
    save_calls = 0

    def failing_order_2(tenant_id: str, order: Order) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("PRIMARY: Order 2 persistence failed")
        real_order_save(tenant_id, order)

    orchestrator._order_repo.save = failing_order_2  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "PRIMARY: Order 2 persistence failed" in str(exc)

    assert caught, "Expected primary RuntimeError was not raised!"

    # Order 0 was still rolled back!
    saved_orders = list(orchestrator._order_repo._orders.get("tenant_01", {}).values())
    assert len(saved_orders) == 1
    assert saved_orders[0].status == OrderStatus.CANCELLED
    assert (
        saved_orders[0].cancellation_reason
        == "Saga compensation: cart checkout persistence failure"
    )

    # Inventory holds were still released!
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and inv_01.reserved == 0
    assert inv_02 is not None and inv_02.reserved == 0

    log("    [+] Probe 7 PASSED: Order rollback and inventory release survived refund failure.")
    return True


def probe_8_domain_order_cancel_invariants() -> bool:
    """Probe 8: Order.cancel() domain invariants.

    Verifies idempotency, non-empty reason validation, and refunded order protection.
    """
    log("[->] Probe 8: Order.cancel() domain model invariant tests...")
    order = Order(
        order_id="ord_test_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(10000, "INR"),
        status=OrderStatus.CONFIRMED,
        payment_attempt_id="att_01",
    )
    assert order.cancellation_reason is None

    # 1. Successful cancellation with valid reason
    cancelled = order.cancel(reason="Saga compensation: test cancellation")
    assert cancelled.status == OrderStatus.CANCELLED
    assert cancelled.cancellation_reason == "Saga compensation: test cancellation"
    assert cancelled.order_id == order.order_id

    # 2. Idempotent cancellation preserves original reason
    retried = cancelled.cancel(reason="Different reason on retry")
    assert retried is cancelled, "Expected retried cancel on CANCELLED order to return self!"
    assert retried.cancellation_reason == "Saga compensation: test cancellation"

    # 3. Empty reason rejection
    caught_empty = False
    try:
        order.cancel(reason="   ")
    except InvalidPaymentError as exc:
        caught_empty = True
        assert "cancellation reason must be a non-empty string" in str(exc)
    assert caught_empty, "Empty reason should be rejected!"

    # 4. Refunded order cancellation rejected
    refunded_order = Order(
        order_id="ord_test_02",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(10000, "INR"),
        status=OrderStatus.REFUNDED,
    )
    caught_refunded = False
    try:
        refunded_order.cancel(reason="Trying to cancel refunded order")
    except InvalidPaymentError as exc:
        caught_refunded = True
        assert "Cannot cancel a refunded order" in str(exc)
    assert caught_refunded, "Cancelling refunded order should be rejected!"

    log("    [+] Probe 8 PASSED: Order domain model cancel invariants verified.")
    return True


def probe_9_sqlalchemy_sqlite_relational_rollback() -> bool:
    """Probe 9: End-to-end relational database (SQLAlchemy) rollback.

    Verifies CANCELLED status and cancellation_reason are persisted to the relational table.
    """
    log("[->] Probe 9: End-to-end relational SQLite order rollback...")
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    sqla_order_repo = SqlAlchemyOrderRepository(session_factory)

    orchestrator, _, _inv_repo = setup_cart_orchestrator()
    orchestrator._order_repo = sqla_order_repo

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, 129900 + 50000)

    real_sqla_save = sqla_order_repo.save
    save_calls = 0

    def faulty_sqla_save(tenant_id_or_order: Any, order: Any = None) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise RuntimeError("Database deadlock on order 2")
        real_sqla_save(tenant_id_or_order, order)

    sqla_order_repo.save = faulty_sqla_save  # type: ignore[method-assign]

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action, tenant_id="tenant_01", cart_id=cart.cart_id
        )
    except RuntimeError as exc:
        caught = True
        assert "Database deadlock on order 2" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # Query the SQLite database directly via SQLAlchemy select
    with session_factory() as session:
        stmt = select(OrderModel).where(OrderModel.tenant_id == "tenant_01")
        models = session.scalars(stmt).all()
        assert len(models) == 1, f"Expected 1 model in database, got {len(models)}"
        assert models[0].status == "CANCELLED"
        assert (
            models[0].cancellation_reason == "Saga compensation: cart checkout persistence failure"
        )
        assert models[0].variant_id == "var_01"

    log("    [+] Probe 9 PASSED: Relational database persisted CANCELLED status & reason.")
    return True


def probe_10_multitenant_isolation_during_rollback() -> bool:
    """Probe 10: Multi-tenant isolation during saga order rollback.

    Verifies orders from Tenant A cannot be rolled back or accessed by Tenant B.
    """
    log("[->] Probe 10: Multi-tenant isolation during order rollback...")
    repo = InMemoryOrderRepository()
    order_a = Order(
        order_id="ord_tenant_a",
        tenant_id="tenant_A",
        merchant_id="merch_01",
        buyer_id="user_a",
        agent_id="agent_a",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(10000, "INR"),
        status=OrderStatus.CONFIRMED,
    )
    repo.save("tenant_A", order_a)

    # Saving with mismatched tenant partition raises CrossTenantAccessError
    cancelled_a = order_a.cancel(reason="Saga compensation: test")
    caught_mismatch = False
    try:
        repo.save("tenant_B", cancelled_a)
    except CrossTenantAccessError:
        caught_mismatch = True

    assert caught_mismatch, "Saving order under wrong tenant partition should fail!"

    # Tenant B cannot see Tenant A's order
    order_b_view = repo.get("tenant_B", "ord_tenant_a")
    assert order_b_view is None

    # Tenant A order is unchanged
    persisted_a = repo.get("tenant_A", "ord_tenant_a")
    assert persisted_a is not None and persisted_a.status == OrderStatus.CONFIRMED

    log("    [+] Probe 10 PASSED: Tenant boundary strictly enforced during order rollback.")
    return True


def main() -> int:
    log("==================================================================")
    log("CHALLENGER HARNESS: BUG-01 MULTI-ITEM CART SAGA ORDER ROLLBACK")
    log("==================================================================")

    probes = [
        (
            "Probe 1: Order save failure rolls back prior orders",
            probe_1_order_save_failure_rolls_back_prior_orders,
        ),
        ("Probe 2: Order save failure in large cart", probe_2_order_save_failure_large_cart),
        (
            "Probe 3: Boundary condition order 0 save failure",
            probe_3_order_save_failure_on_first_order,
        ),
        (
            "Probe 4: Cart save failure rolls back all orders",
            probe_4_cart_save_failure_rolls_back_all_orders,
        ),
        (
            "Probe 5: Secondary error suppression on order save",
            probe_5_secondary_error_masking_prevention_on_order_save,
        ),
        (
            "Probe 6: Secondary error suppression on cart save",
            probe_6_secondary_error_masking_prevention_on_cart_save,
        ),
        (
            "Probe 7: Payment refund failure resilience",
            probe_7_refund_failure_does_not_abort_rollback,
        ),
        ("Probe 8: Order domain model cancel invariants", probe_8_domain_order_cancel_invariants),
        (
            "Probe 9: Relational SQLite order rollback persistence",
            probe_9_sqlalchemy_sqlite_relational_rollback,
        ),
        (
            "Probe 10: Multi-tenant isolation during rollback",
            probe_10_multitenant_isolation_during_rollback,
        ),
    ]

    all_passed = True
    for name, probe_fn in probes:
        try:
            passed = probe_fn()
            if not passed:
                log(f"[!] FAILED: {name}")
                all_passed = False
        except Exception as exc:
            log(f"[!] EXCEPTION in {name}: {type(exc).__name__}: {exc}")
            import traceback

            traceback.print_exc()
            all_passed = False

    log("==================================================================")
    if all_passed:
        log("[+] ALL 10 EMPIRICAL CHALLENGER PROBES PASSED -> VERDICT: APPROVE")
        return 0
    else:
        log("[-] ONE OR MORE PROBES FAILED -> VERDICT: REJECT")
        return 1


if __name__ == "__main__":
    sys.exit(main())

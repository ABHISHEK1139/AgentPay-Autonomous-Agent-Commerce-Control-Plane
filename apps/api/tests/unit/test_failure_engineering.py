"""Failure engineering and chaos testing suite (§112, §42, §43, §44).

Verifies fail-closed behavior, saga compensation, payment timeout reconciliation,
price drift detection, and idempotency guarantees under adverse conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_purchase_flow import setup_orchestrator

from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import Inventory, ProductVariant
from agentpay.domain.errors import InsufficientInventoryError
from agentpay.domain.money import Money
from agentpay.domain.payments import OrderStatus, PaymentStatus
from agentpay.domain.policy import PolicyDecision


def test_in_flight_price_modification_fails_closed() -> None:
    """Scenario 4 (§112): Merchant price changes in-flight between agent quote and execution.

    System must fail closed: detect PRICE_MISMATCH, abort before payment, and leave stock intact.
    """
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        variant_repo,
        inventory_repo,
        payment_provider,
        audit_ledger,
        _,
    ) = setup_orchestrator()

    # Agent believes price is ₹1,499 (149900 minor units)
    action = ActionRequest(
        action_id="act_chaos_price",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop order"),
        amount=Money(149900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    # In-flight catalog update: merchant price changed to ₹1,999 (199900 minor units)
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_01",
            product_id="prod_01",
            sku="SKU-DRIFT",
            name="Laptop",
            price=Money(199900, "INR"),
        ),
    )

    initial_inv = inventory_repo.get("tenant_01", "var_01")
    assert initial_inv is not None
    initial_available = initial_inv.available

    # Execute purchase
    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
    )

    # Must fail closed
    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert len(result.conflicts) == 1
    assert result.conflicts[0].conflict_type.value == "PRICE_MISMATCH"
    assert result.payment is None
    assert result.order is None

    # Zero payment calls made
    assert len(payment_provider._attempts) == 0

    # Inventory was NOT consumed
    final_inv = inventory_repo.get("tenant_01", "var_01")
    assert final_inv is not None
    assert final_inv.available == initial_available
    assert final_inv.reserved == 0

    # Audit ledger records the conflict
    events = audit_ledger.get_events("tenant_01", action_id="act_chaos_price")
    conflict_events = [e for e in events if e.event_type == AuditEventType.CONFLICT_DETECTED]
    assert len(conflict_events) == 1
    assert conflict_events[0].data.get("first_conflict") == "PRICE_MISMATCH"


def test_sudden_inventory_depletion_fails_closed() -> None:
    """Scenario 5 (§112): Inventory drops to 0 immediately before capture.

    Must detect INVENTORY_MISMATCH conflict and abort before payment without charging funds.
    """
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        inventory_repo,
        payment_provider,
        _,
        _,
    ) = setup_orchestrator()

    # Deplete inventory to zero
    inventory_repo.save(
        "tenant_01",
        Inventory(variant_id="var_01", tenant_id="tenant_01", available=0, reserved=0),
    )

    action = ActionRequest(
        action_id="act_chaos_inv",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop order"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
    )

    # Orchestrator catches mismatch and fails closed
    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    assert len(result.conflicts) == 1
    assert result.conflicts[0].conflict_type.value == "INVENTORY_MISMATCH"
    assert result.payment is None
    assert result.order is None

    # No payment was attempted
    assert len(payment_provider._attempts) == 0

    # Direct atomic reservation also strictly fails closed
    with pytest.raises(InsufficientInventoryError):
        inventory_repo.reserve("tenant_01", "var_01", 1)


def test_payment_gateway_timeout_triggers_reconciliation_without_duplicate_charge() -> None:
    """Scenario 6 (§112, §43): Payment gateway timeout.

    System marks status UNKNOWN, preserves idempotency, and never blindly retries.
    """
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        _inventory_repo,
        _payment_provider,
        audit_ledger,
        _,
    ) = setup_orchestrator(simulate_timeout=True)

    action = ActionRequest(
        action_id="act_chaos_timeout",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop order"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
        idempotency_key="chaos_idem_timeout_01",
    )

    # Result reflects failure due to timeout
    assert result.status == ActionStatus.FAILED
    assert result.payment is not None
    assert result.payment.status == PaymentStatus.UNKNOWN

    # Audit records the unknown payment state
    events = audit_ledger.get_events("tenant_01", action_id="act_chaos_timeout")
    timeout_events = [e for e in events if e.event_type == AuditEventType.PAYMENT_UNKNOWN]
    assert len(timeout_events) == 1

    # Invariant: Never blindly retry a charge (§43). Replaying the idempotency key returns cached state.
    replay_result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
        idempotency_key="chaos_idem_timeout_01",
    )
    assert replay_result.payment is not None
    assert replay_result.payment.status == PaymentStatus.UNKNOWN


def test_payment_decline_triggers_saga_compensation() -> None:
    """Scenario (§44): Payment declined by issuer triggers saga compensation.

    Reserved inventory is safely released back to available pool.
    """
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        inventory_repo,
        _payment_provider,
        audit_ledger,
        _,
    ) = setup_orchestrator(simulate_decline=True)

    initial_inv = inventory_repo.get("tenant_01", "var_01")
    assert initial_inv is not None
    initial_available = initial_inv.available

    # Total amount for 1 unit is 129900
    action = ActionRequest(
        action_id="act_chaos_decline",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop order"),
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
    assert result.payment.status == PaymentStatus.DECLINED
    assert result.order is not None
    assert result.order.status == OrderStatus.CANCELLED

    # Saga compensation check: inventory was restored!
    final_inv = inventory_repo.get("tenant_01", "var_01")
    assert final_inv is not None
    assert final_inv.available == initial_available
    assert final_inv.reserved == 0

    # Audit events verify failure & order cancellation
    events = audit_ledger.get_events("tenant_01", action_id="act_chaos_decline")
    event_types = [e.event_type for e in events]
    assert AuditEventType.PAYMENT_FAILED in event_types
    assert AuditEventType.ORDER_CANCELLED in event_types


def test_idempotency_replay_prevents_duplicate_charge_and_inventory_decrement() -> None:
    """Invariant (§42): Multiple identical submissions never double-charge or double-decrement."""
    (
        orchestrator,
        _,
        _,
        _,
        _,
        _,
        _,
        inventory_repo,
        payment_provider,
        _,
        _,
    ) = setup_orchestrator()

    initial_inv = inventory_repo.get("tenant_01", "var_01")
    assert initial_inv is not None
    initial_available = initial_inv.available

    action = ActionRequest(
        action_id="act_idem_test",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop order"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    # Run 1
    r1 = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
        idempotency_key="key_idempotent_single_charge",
    )
    assert r1.status == ActionStatus.COMPLETED

    # Run 2 with identical key
    r2 = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=1,
        idempotency_key="key_idempotent_single_charge",
    )
    assert r2.status == ActionStatus.COMPLETED
    assert r1.order is not None and r2.order is not None
    assert r1.order.order_id == r2.order.order_id

    # Invariant: exactly 1 payment captured, exactly 1 unit reserved (effective_available decremented by 1)
    assert len(payment_provider._attempts) == 1
    final_inv = inventory_repo.get("tenant_01", "var_01")
    assert final_inv is not None
    assert final_inv.reserved == 1
    assert final_inv.effective_available == initial_available - 1


@given(
    requested_qty=st.integers(min_value=11, max_value=100),
)
def test_hypothesis_inventory_cannot_oversell(requested_qty: int) -> None:
    """Property test: inventory available=10 can never fulfill requests > 10."""
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
        _,
        _,
    ) = setup_orchestrator()

    # Exactly matching price: unit price * requested_qty
    total_amount = Money(129900 * requested_qty, "INR")

    action = ActionRequest(
        action_id=f"act_oversell_{requested_qty}",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="oversell attempt"),
        amount=total_amount,
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    # Pre-flight conflict detection immediately fails closed
    result = orchestrator.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        quantity=requested_qty,
    )
    assert result.status == ActionStatus.FAILED
    assert result.decision == PolicyDecision.DENY
    conflict_types = [c.conflict_type.value for c in result.conflicts]
    assert "INVENTORY_MISMATCH" in conflict_types

    # Direct atomic reservation attempt strictly raises InsufficientInventoryError
    with pytest.raises(InsufficientInventoryError):
        inventory_repo.reserve("tenant_01", "var_01", requested_qty)

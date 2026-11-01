"""Empirical Challenger Stress Harness for BUG-02 (ADR-0006, §44).

Empirically tests and challenges:
1. Multi-item cart reservation operational exception on line K releases lines 0..K-1.
2. Multi-item cart reservation operational exception on line K releases lines 0..K-1 (Approved).
3. Multi-item cart reservation operational exception on line K=0 releases 0 lines safely.
4. 5-item cart reservation operational exception on line K=4 releases lines 0..3.
5. InsufficientInventoryError on line K (TOCTOU race) releases lines 0..K-1 and returns clean DENY.
6. Secondary release failures logged and suppressed without masking primary exception.
7. Transient failure recovery allows clean subsequent checkout on active cart.
8. Concurrent checkouts under fault injection maintain stock conservation invariants.
"""

from __future__ import annotations

import random
import sys
import threading
from pathlib import Path
from typing import cast

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))
sys.path.insert(0, str(Path("apps/worker/src").resolve()))

from test_cart_flow import (  # type: ignore[import-not-found]
    checkout_action,
    setup_cart_orchestrator,
)

from agentpay.domain.action_request import ActionRequest, ActionStatus
from agentpay.domain.audit import AuditEventType
from agentpay.domain.cart import CartStatus
from agentpay.domain.commerce import Inventory, ProductVariant
from agentpay.domain.errors import InsufficientInventoryError
from agentpay.domain.identity import UserRole
from agentpay.domain.money import Money
from agentpay.domain.payments import PaymentStatus
from agentpay.domain.policy import PolicyDecision


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def probe_autonomous_operational_failure_line_k1() -> bool:
    """Probe 1: Autonomous flow operational failure on line K=1 in a 2-item cart."""
    log("[->] Probe 1: Autonomous operational failure on line 1 releases line 0...")
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    inv_01_init = inventory_repo.get("tenant_01", "var_01")
    inv_02_init = inventory_repo.get("tenant_01", "var_02")
    assert inv_01_init is not None and (inv_01_init.available, inv_01_init.reserved) == (10, 0)
    assert inv_02_init is not None and (inv_02_init.available, inv_02_init.reserved) == (3, 0)

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_02":
            raise RuntimeError("Database connection dropped during inventory reservation on var_02")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = failing_reserve
    action = checkout_action(cart.cart_id, 129900 + 50000)

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )
    except RuntimeError as exc:
        caught = True
        assert "Database connection dropped" in str(exc)

    assert caught, "Expected RuntimeError was not raised!"

    # Verify line 0 compensated
    inv_01 = inventory_repo.get("tenant_01", "var_01")
    assert inv_01 is not None
    assert (inv_01.available, inv_01.reserved) == (10, 0), f"Line 0 stock not restored: {inv_01}"

    # Verify line 1 never reserved
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_02 is not None
    assert (inv_02.available, inv_02.reserved) == (3, 0), f"Line 1 stock not clean: {inv_02}"

    # Verify cart remains ACTIVE
    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE

    # Verify ORDER_CANCELLED audit event
    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    reason = str(cancelled[-1].data.get("reason"))
    assert "Database connection dropped" in reason, f"Unexpected reason: {reason}"
    log("[*] Probe 1: PASS")
    return True


def probe_approved_operational_failure_line_k1() -> bool:
    """Probe 2: Approved flow operational failure on line K=1 in a 2-item cart."""
    log("[->] Probe 2: Approved flow operational failure on line 1 releases line 0...")
    orchestrator, _cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=2)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    action = checkout_action(cart.cart_id, (2 * 129900) + 50000)
    initial_res = orchestrator.execute_cart_purchase(
        action=action, tenant_id="tenant_01", cart_id=cart.cart_id
    )
    assert initial_res.decision == PolicyDecision.REVIEW
    assert initial_res.approval is not None

    granted = orchestrator._approval_service.grant_approval(
        approval_id=initial_res.approval.approval_id,
        approver_id="manager_01",
        tenant_id="tenant_01",
        approver_role=UserRole.ADMIN,
        caller_principal_id="manager_01",
    )

    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_02":
            raise RuntimeError("Database socket timeout on approved purchase on var_02")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = failing_reserve

    action_approved = ActionRequest(
        action_id=granted.binding.action_id,
        tenant_id=action.tenant_id,
        principal_id=action.principal_id,
        agent_id=action.agent_id,
        domain=action.domain,
        action=action.action,
        resource=action.resource,
        requested_at=action.requested_at,
        intent=action.intent,
        amount=action.amount,
        authorization_mode=action.authorization_mode,
        version=granted.binding.action_version,
    )

    caught = False
    try:
        orchestrator.execute_approved_cart_purchase(
            approval_id=granted.approval_id,
            action=action_approved,
            tenant_id="tenant_01",
        )
    except RuntimeError as exc:
        caught = True
        assert "Database socket timeout" in str(exc)

    assert caught, "Expected RuntimeError in approved purchase was not raised!"

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    assert inv_01 is not None
    assert (inv_01.available, inv_01.reserved) == (10, 0), f"Line 0 stock not restored: {inv_01}"

    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_02 is not None
    assert (inv_02.available, inv_02.reserved) == (3, 0), f"Line 1 stock not clean: {inv_02}"

    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    assert "Database socket timeout on approved purchase" in str(cancelled[-1].data.get("reason"))
    log("[*] Probe 2: PASS")
    return True


def probe_operational_failure_line_k0() -> bool:
    """Probe 3: Operational failure on line K=0 (first item) in a multi-item cart."""
    log("[->] Probe 3: Operational failure on line 0 (first item)...")
    orchestrator, _cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_01":
            raise ConnectionError("DB connection refused immediately on var_01")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = failing_reserve
    action = checkout_action(cart.cart_id, 129900 + 50000)

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )
    except ConnectionError as exc:
        caught = True
        assert "DB connection refused" in str(exc)

    assert caught, "Expected ConnectionError was not raised!"

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)

    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    reason = str(cancelled[-1].data.get("reason"))
    assert "DB connection refused" in reason
    log("[*] Probe 3: PASS")
    return True


def probe_operational_failure_line_k4_five_items() -> bool:
    """Probe 4: 5-item cart fails on line K=4 (the last item), releasing lines 0..3."""
    log("[->] Probe 4: 5-item cart fails on line 4, releasing lines 0..3...")
    orchestrator, _cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None
    variant_repo = orchestrator._variant_repo
    assert variant_repo is not None

    for v_id, price, stock in (
        ("var_01", 20000, 10),
        ("var_02", 30000, 5),
        ("var_04", 40000, 10),
        ("var_06", 20000, 15),
        ("var_07", 30000, 20),
    ):
        variant_repo.save(
            "tenant_01",
            ProductVariant(
                variant_id=v_id,
                product_id="prod_01",
                sku=f"SKU-{v_id}",
                name=f"Variant {v_id}",
                price=Money(price, "INR"),
            ),
        )
        inventory_repo.save(
            "tenant_01",
            Inventory(variant_id=v_id, tenant_id="tenant_01", available=stock, reserved=0),
        )

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_04", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_06", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_07", quantity=1)

    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_07":
            raise TimeoutError("Inventory lock timeout on 5th item var_07")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = failing_reserve
    total_price = 20000 + 30000 + 40000 + 20000 + 30000
    action = checkout_action(cart.cart_id, total_price)

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )
    except TimeoutError as exc:
        caught = True
        assert "Inventory lock timeout" in str(exc)

    assert caught, "Expected TimeoutError was not raised!"

    for v_id, expected_avail in (
        ("var_01", 10),
        ("var_02", 5),
        ("var_04", 10),
        ("var_06", 15),
        ("var_07", 20),
    ):
        inv = inventory_repo.get("tenant_01", v_id)
        assert inv is not None
        assert (inv.available, inv.reserved) == (expected_avail, 0), (
            f"{v_id} expected ({expected_avail}, 0) but got ({inv.available}, {inv.reserved})"
        )

    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    reason = str(cancelled[-1].data.get("reason"))
    assert "Inventory lock timeout on 5th item var_07" in reason
    log("[*] Probe 4: PASS")
    return True


def probe_insufficient_inventory_clean_rejection() -> bool:
    """Probe 5: InsufficientInventoryError on line K releases lines 0..K-1 cleanly."""
    log("[->] Probe 5: InsufficientInventoryError releases prior lines and returns clean DENY...")
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    real_reserve = inventory_repo.reserve

    def race_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_02":
            raise InsufficientInventoryError(
                f"Variant '{variant_id}' has 0 available, requested {qty}"
            )
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = race_reserve

    action = checkout_action(cart.cart_id, 129900 + 50000)

    result = orchestrator.execute_cart_purchase(
        action=action,
        tenant_id="tenant_01",
        cart_id=cart.cart_id,
    )

    assert result.status == ActionStatus.FAILED, f"Expected FAILED but got {result.status}"
    assert result.decision == PolicyDecision.DENY, f"Expected DENY but got {result.decision}"
    assert result.rejection_reason is not None
    assert "has 0 available" in result.rejection_reason

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)

    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)

    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE

    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    assert "has 0 available" in str(cancelled[-1].data.get("reason"))
    log("[*] Probe 5: PASS")
    return True


def probe_secondary_release_failure_suppression() -> bool:
    """Probe 6: Secondary failure during inventory release does not mask primary error."""
    log("[->] Probe 6: Secondary release failure does not mask primary exception...")
    orchestrator, _, inventory_repo = setup_cart_orchestrator()
    audit_ledger = orchestrator._audit_ledger
    assert audit_ledger is not None

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    real_reserve = inventory_repo.reserve

    def failing_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        if variant_id == "var_02":
            raise RuntimeError("PRIMARY: DB connection crashed on line 1")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    def failing_release(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        raise ConnectionResetError("SECONDARY: Network dropped during compensation release")

    inventory_repo.reserve = failing_reserve
    inventory_repo.release = failing_release

    action = checkout_action(cart.cart_id, 129900 + 50000)

    caught_primary = False
    try:
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )
    except RuntimeError as exc:
        if "PRIMARY: DB connection crashed" in str(exc):
            caught_primary = True

    assert caught_primary, "Primary exception was masked by secondary compensation error!"

    events = audit_ledger.get_events("tenant_01")
    cancelled = [e for e in events if e.event_type is AuditEventType.ORDER_CANCELLED]
    assert len(cancelled) >= 1
    assert "PRIMARY: DB connection crashed" in str(cancelled[-1].data.get("reason"))
    log("[*] Probe 6: PASS")
    return True


def probe_transient_failure_then_successful_retry() -> bool:
    """Probe 7: Transient failure compensates properly, allowing clean subsequent retry."""
    log("[->] Probe 7: Transient failure recovery and successful retry...")
    orchestrator, cart_repo, inventory_repo = setup_cart_orchestrator()

    cart = orchestrator.create_cart(
        tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
    )
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
    cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)

    real_reserve = inventory_repo.reserve
    fail_once = True

    def transient_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        nonlocal fail_once
        if variant_id == "var_02" and fail_once:
            fail_once = False
            raise RuntimeError("Transient deadlock on var_02")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = transient_reserve
    action = checkout_action(cart.cart_id, 129900 + 50000, action_id="act_transient_01")

    caught = False
    try:
        orchestrator.execute_cart_purchase(
            action=action,
            tenant_id="tenant_01",
            cart_id=cart.cart_id,
        )
    except RuntimeError:
        caught = True
    assert caught

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and (inv_01.available, inv_01.reserved) == (10, 0)
    assert inv_02 is not None and (inv_02.available, inv_02.reserved) == (3, 0)

    persisted = cart_repo.get("tenant_01", cart.cart_id)
    assert persisted is not None and persisted.status is CartStatus.ACTIVE

    action_retry = checkout_action(cart.cart_id, 129900 + 50000, action_id="act_transient_02")
    result = orchestrator.execute_cart_purchase(
        action=action_retry,
        tenant_id="tenant_01",
        cart_id=cart.cart_id,
    )

    assert result.status == ActionStatus.COMPLETED
    assert result.payment is not None and result.payment.status == PaymentStatus.CAPTURED
    assert len(result.orders) == 2

    inv_01_after = inventory_repo.get("tenant_01", "var_01")
    inv_02_after = inventory_repo.get("tenant_01", "var_02")
    assert inv_01_after is not None and (inv_01_after.available, inv_01_after.reserved) == (10, 1)
    assert inv_02_after is not None and (inv_02_after.available, inv_02_after.reserved) == (3, 1)
    log("[*] Probe 7: PASS")
    return True


def probe_concurrent_fault_injected_checkouts() -> bool:
    """Probe 8: Concurrent checkouts with fault injection maintain stock conservation."""
    log("[->] Probe 8: Concurrent checkouts with fault injection (stock conservation)...")
    orchestrator, _, inventory_repo = setup_cart_orchestrator()

    real_reserve = inventory_repo.reserve
    lock = threading.Lock()
    injected_failures = 0

    def faulty_reserve(tenant_id: str, variant_id: str, qty: int) -> Inventory:
        nonlocal injected_failures
        if variant_id == "var_02" and random.random() < 0.35:
            with lock:
                injected_failures += 1
            raise RuntimeError("Injected transient DB fault during reserve")
        return cast(Inventory, real_reserve(tenant_id, variant_id, qty))

    inventory_repo.reserve = faulty_reserve

    num_threads = 6
    threads: list[threading.Thread] = []
    completed_checkouts = 0
    operational_failures = 0
    insufficient_inv = 0

    def worker_run(worker_id: int) -> None:
        nonlocal completed_checkouts, operational_failures, insufficient_inv
        try:
            cart = orchestrator.create_cart(
                tenant_id="tenant_01", agent_id="agent_01", principal_id="user_01"
            )
            cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_01", quantity=1)
            cart = orchestrator.add_cart_item("tenant_01", cart.cart_id, "var_02", quantity=1)
            action = checkout_action(
                cart.cart_id, 129900 + 50000, action_id=f"act_conc_fault_{worker_id}"
            )
            res = orchestrator.execute_cart_purchase(
                action=action,
                tenant_id="tenant_01",
                cart_id=cart.cart_id,
            )
            if res.status == ActionStatus.COMPLETED:
                with lock:
                    completed_checkouts += 1
            else:
                with lock:
                    insufficient_inv += 1
        except RuntimeError:
            with lock:
                operational_failures += 1

    for i in range(num_threads):
        t = threading.Thread(target=worker_run, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=30)

    log(
        f"    completed={completed_checkouts}, operational_failures={operational_failures}, "
        f"insufficient_inv={insufficient_inv}, total_threads={num_threads}"
    )

    inv_01 = inventory_repo.get("tenant_01", "var_01")
    inv_02 = inventory_repo.get("tenant_01", "var_02")
    assert inv_01 is not None and inv_02 is not None

    assert inv_01.reserved == completed_checkouts, (
        f"var_01 reserved ({inv_01.reserved}) != completed checkouts ({completed_checkouts})"
    )
    assert inv_02.reserved == completed_checkouts, (
        f"var_02 reserved ({inv_02.reserved}) != completed checkouts ({completed_checkouts})"
    )
    assert inv_01.effective_available == inv_01.available - inv_01.reserved
    assert inv_02.effective_available == inv_02.available - inv_02.reserved

    log("[*] Probe 8: PASS (Stock conservation invariant held perfectly)")
    return True


def main() -> int:
    log("==================================================================")
    log("BUG-02 EMPIRICAL CHALLENGER STRESS HARNESS")
    log("==================================================================")
    results = [
        probe_autonomous_operational_failure_line_k1(),
        probe_approved_operational_failure_line_k1(),
        probe_operational_failure_line_k0(),
        probe_operational_failure_line_k4_five_items(),
        probe_insufficient_inventory_clean_rejection(),
        probe_secondary_release_failure_suppression(),
        probe_transient_failure_then_successful_retry(),
        probe_concurrent_fault_injected_checkouts(),
    ]
    if all(results):
        log("==================================================================")
        log("[+] RESULT: ALL 8 BUG-02 CHALLENGER PROBES PASSED -> APPROVE")
        log("==================================================================")
        return 0
    log("[-] RESULT: PROBES FAILED -> REJECT")
    return 1


if __name__ == "__main__":
    sys.exit(main())

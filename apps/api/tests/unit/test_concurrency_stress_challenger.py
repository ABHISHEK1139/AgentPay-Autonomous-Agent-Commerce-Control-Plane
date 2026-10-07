"""Concurrency and Stress Challenger Verification Suite (§177, Vectors 2, 10, 11).

Empirically tests concurrency, race conditions, and security invariants:
1. Vector 11: Inventory race conditions under heavy multithreaded contention (50 concurrent threads racing for 5 items, Barrier-synchronized).
2. Vector 10: Multithreaded concurrent approval consumption (50 threads racing to consume the same approval).
3. Vector 2: Idempotency conflict under rapid concurrent replay (identical replays and concurrent mutated payload conflicts).
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import UTC, datetime
from typing import Any

from test_purchase_flow import setup_orchestrator

from agentpay.application.services.approval_service import ApprovalService
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import (
    ApprovalAlreadyConsumedError,
    ApprovalStatus,
)
from agentpay.domain.commerce import (
    Inventory,
    ProductVariant,
)
from agentpay.domain.errors import (
    IdempotencyConflictError,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import PolicyDecision


# ===========================================================================
# Vector 11: Heavy Multithreaded Inventory Contention (50 threads vs 5 items)
# ===========================================================================
def test_vector_11_heavy_multithreaded_inventory_contention_50_threads() -> None:
    """Stress-test: 50 concurrent threads simultaneously race for 5 inventory items using a Barrier."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, _ = setup_orchestrator()

    # Seed variant and exactly 5 available items
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_scarce_50",
            product_id="prod_01",
            sku="SKU-SCARCE-50",
            name="Scarce 50 Item",
            price=Money(100000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_scarce_50", tenant_id="tenant_01", available=5, reserved=0),
    )

    num_threads = 50
    barrier = threading.Barrier(num_threads)

    def racer(idx: int) -> tuple[int, ActionStatus, str | None]:
        action = ActionRequest(
            action_id=f"act_race50_{idx}",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product", id="prod_01"),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="race 50 contention"),
            amount=Money(100000, "INR"),
        )
        # Synchronize all threads so they launch the execute call simultaneously
        barrier.wait()
        res = orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_scarce_50",
        )
        return idx, res.status, res.rejection_reason

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    successes = [r for r in results if r[1] == ActionStatus.COMPLETED]
    failures = [r for r in results if r[1] == ActionStatus.FAILED]

    # Invariants under heavy contention:
    # 1. Exactly 5 purchases must succeed
    assert len(successes) == 5, f"Expected exactly 5 successes, got {len(successes)}"
    # 2. Exactly 45 purchases must fail
    assert len(failures) == 45, f"Expected exactly 45 failures, got {len(failures)}"

    # 3. Exactly 5 payment attempts created
    assert len(payment_provider._attempts) == 5

    # 4. Final inventory invariants
    final_inv = inv_repo.get("tenant_01", "var_scarce_50")
    assert final_inv is not None
    assert final_inv.available == 5
    assert final_inv.reserved == 5
    assert final_inv.effective_available == 0


def test_vector_11_multi_unit_heavy_contention() -> None:
    """Stress-test: 20 concurrent threads requesting 2 units each when only 7 are available."""
    orch, _, _, _, _, _, variant_repo, inv_repo, _, _, _ = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_multi_7",
            product_id="prod_01",
            sku="SKU-MULTI-7",
            name="Multi Unit Item",
            price=Money(50000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_multi_7", tenant_id="tenant_01", available=7, reserved=0),
    )

    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def racer(idx: int) -> tuple[int, ActionStatus]:
        action = ActionRequest(
            action_id=f"act_multi_{idx}",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product", id="prod_01"),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="multi-unit purchase"),
            amount=Money(100000, "INR"),  # 2 * 50000
        )
        barrier.wait()
        res = orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_multi_7",
            quantity=2,
        )
        return idx, res.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    successes = [r for r in results if r[1] == ActionStatus.COMPLETED]
    failures = [r for r in results if r[1] == ActionStatus.FAILED]

    # Only 3 orders can be fulfilled (3 * 2 = 6 units). 1 unit remains, cannot fulfill 2 units.
    assert len(successes) == 3
    assert len(failures) == 17

    final_inv = inv_repo.get("tenant_01", "var_multi_7")
    assert final_inv is not None
    assert final_inv.reserved == 6
    assert final_inv.effective_available == 1


# ===========================================================================
# Vector 10: Multithreaded Concurrent Approval Consumption Race Conditions
# ===========================================================================
def test_vector_10_concurrent_multithreaded_approval_consumption_single_winner() -> None:
    """Stress-test: 20 concurrent threads race to consume the same human approval simultaneously.

    Security Invariant (§20, §0.4):
    An approval binds to a single execution.
    Under heavy concurrent race conditions, EXACTLY ONE thread must succeed in consuming
    the approval, and all other threads must fail (e.g. ApprovalAlreadyConsumedError).
    Under NO circumstance may payment be charged twice.
    """
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, approval_repo = (
        setup_orchestrator()
    )

    # Variant ₹8,000 requires human approval
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_appr_concurrent",
            product_id="prod_01",
            sku="SKU-APPR-CONC",
            name="Approval Item Concurrent",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_appr_concurrent", tenant_id="tenant_01", available=10, reserved=0
        ),
    )

    action = ActionRequest(
        action_id="act_v10_conc",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="high cost asset race"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    # Step 1: Initial evaluation requests approval
    r_review = orch.execute_purchase(
        action=action, tenant_id="tenant_01", variant_id="var_appr_concurrent"
    )
    assert r_review.decision == PolicyDecision.REVIEW
    assert r_review.approval is not None
    appr_id = r_review.approval.approval_id

    # Step 2: Human approver grants approval
    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(
        approval_id=appr_id, approver_id="approver_bob", tenant_id="tenant_01"
    )

    # Step 3: Launch 20 concurrent threads racing to execute the same approved action
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def consume_racer(idx: int) -> tuple[int, str, Any]:
        barrier.wait()
        try:
            res = orch.execute_approved_purchase(
                approval_id=appr_id,
                action=action,
                tenant_id="tenant_01",
                variant_id="var_appr_concurrent",
                idempotency_key=f"idem_conc_appr_{idx}",
            )
            return idx, "COMPLETED" if res.status == ActionStatus.COMPLETED else "FAILED", res
        except ApprovalAlreadyConsumedError as err:
            return idx, "CONSUMED_ERROR", str(err)
        except Exception as exc:
            return idx, "OTHER_ERROR", type(exc).__name__

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(consume_racer, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    completed_count = sum(1 for r in results if r[1] == "COMPLETED")
    consumed_error_count = sum(1 for r in results if r[1] == "CONSUMED_ERROR")

    # Invariant: EXACTLY 1 winner may execute the approved purchase!
    assert completed_count == 1, (
        f"CRITICAL RACE CONDITION: Expected exactly 1 winner, but {completed_count} completed!"
    )
    assert consumed_error_count == num_threads - 1, (
        f"Expected {num_threads - 1} consumed errors, got {consumed_error_count}"
    )

    # Payment provider attempts MUST BE EXACTLY 1
    assert len(payment_provider._attempts) == 1

    # Approval state in repository MUST be CONSUMED
    final_appr = approval_repo.get("tenant_01", appr_id)
    assert final_appr is not None
    assert final_appr.status == ApprovalStatus.CONSUMED


def test_vector_10_approval_consumption_toctou_race_with_db_latency() -> None:
    """Verify that atomic check-and-consume prevents TOCTOU race even under simulated DB latency."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, approval_repo = (
        setup_orchestrator()
    )

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_toctou",
            product_id="prod_01",
            sku="SKU-TOCTOU",
            name="TOCTOU Item",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_toctou", tenant_id="tenant_01", available=100, reserved=0),
    )

    action = ActionRequest(
        action_id="act_v10_toctou",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="toctou race"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    r_review = orch.execute_purchase(action=action, tenant_id="tenant_01", variant_id="var_toctou")
    assert r_review.approval is not None
    appr_id = r_review.approval.approval_id

    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(
        approval_id=appr_id, approver_id="approver_bob", tenant_id="tenant_01"
    )

    orig_consume = approval_repo.atomic_consume

    def slow_consume(t_id: str, a_id: str) -> Any:
        time.sleep(0.005)  # 5ms simulated latency
        return orig_consume(t_id, a_id)

    approval_repo.atomic_consume = slow_consume  # type: ignore[assignment]

    num_threads = 5
    barrier = threading.Barrier(num_threads)

    def racer(idx: int) -> str:
        barrier.wait()
        try:
            res = orch.execute_approved_purchase(
                approval_id=appr_id,
                action=action,
                tenant_id="tenant_01",
                variant_id="var_toctou",
                idempotency_key=f"idem_toctou_{idx}",
            )
            return "COMPLETED" if res.status == ActionStatus.COMPLETED else "FAILED"
        except ApprovalAlreadyConsumedError:
            return "CONSUMED"
        except Exception:
            return "ERROR"

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer, i) for i in range(num_threads)]
        outcomes = [f.result() for f in concurrent.futures.as_completed(futures)]

    completed = sum(1 for o in outcomes if o == "COMPLETED")
    assert completed == 1, f"Expected 1 winner, but {completed} succeeded!"
    assert len(payment_provider._attempts) == 1


# ===========================================================================
# Vector 2: Rapid Concurrent Idempotency Replay & Mutated Payloads
# ===========================================================================
def test_vector_02_rapid_concurrent_identical_replay() -> None:
    """Stress-test: 20 concurrent threads simultaneously send the IDENTICAL idempotency key."""
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator()

    action = ActionRequest(
        action_id="act_v02_rapid_replay",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="rapid replay test"),
        amount=Money(minor_units=129900, currency="INR"),
    )

    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def racer(idx: int) -> tuple[int, ActionStatus]:
        barrier.wait()
        res = orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_01",
            idempotency_key="key_burst_identical_01",
        )
        return idx, res.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All calls should complete cleanly
    for _idx, status in results:
        assert status == ActionStatus.COMPLETED

    # Payment provider attempts MUST be exactly 1 due to idempotency
    assert len(payment_provider._attempts) == 1


def test_vector_02_concurrent_mutated_payload_race() -> None:
    """Stress-test: Thread A and Thread B concurrently send MUTATED payloads with the SAME idempotency key."""
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator()

    action_orig = ActionRequest(
        action_id="act_v02_orig",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="legit purchase"),
        amount=Money(minor_units=129900, currency="INR"),
    )

    action_mutated = ActionRequest(
        action_id="act_v02_orig",  # same action_id but mutated amount
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="tampered amount"),
        amount=Money(minor_units=999900, currency="INR"),
    )

    # First, let original request complete and register idempotency record
    r_orig = orch.execute_purchase(
        action=action_orig,
        tenant_id="tenant_01",
        variant_id="var_01",
        idempotency_key="key_race_mutated",
    )
    assert r_orig.status == ActionStatus.COMPLETED

    # Now, rapid concurrent replays with the mutated payload must fail closed with IdempotencyConflictError
    num_threads = 10
    barrier = threading.Barrier(num_threads)

    def racer_mutated(idx: int) -> tuple[int, str]:
        barrier.wait()
        try:
            orch.execute_purchase(
                action=action_mutated,
                tenant_id="tenant_01",
                variant_id="var_01",
                idempotency_key="key_race_mutated",
            )
            return idx, "COMPLETED"
        except IdempotencyConflictError:
            return idx, "CONFLICT"
        except Exception as e:
            return idx, type(e).__name__

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer_mutated, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # 100% of mutated replays must raise IdempotencyConflictError
    for _idx, outcome in results:
        assert outcome == "CONFLICT"

    # Payment provider attempts must remain exactly 1
    assert len(payment_provider._attempts) == 1

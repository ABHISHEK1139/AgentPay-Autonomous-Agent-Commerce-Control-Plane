"""Empirical stress-testing harness for Concurrency, Approval Consumption, and Idempotency (§177).

Exhaustively probes:
1. Vector 11: Inventory race conditions under heavy multithreaded contention (50 threads racing for 5 items).
2. Vector 10: Multithreaded concurrent human approval consumption (testing atomic single-consumer invariant).
3. Vector 2: Idempotency replay under rapid concurrent execution (identical and mutated payloads).
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

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
)
from agentpay.domain.commerce import (
    Inventory,
    ProductVariant,
)
from agentpay.domain.errors import (
    IdempotencyConflictError,
)
from agentpay.domain.money import Money


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ===========================================================================
# Probe 1 & 2: Vector 11 — Inventory Contention
# ===========================================================================
def probe_vector_11_inventory_contention_50_threads() -> dict[str, Any]:
    """Test 50 threads racing for 5 items simultaneously."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, _ = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_probe_50",
            product_id="prod_01",
            sku="SKU-PROBE-50",
            name="Probe 50",
            price=Money(100000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_probe_50", tenant_id="tenant_01", available=5, reserved=0),
    )

    num_threads = 50
    barrier = threading.Barrier(num_threads)

    def racer(idx: int) -> tuple[int, ActionStatus]:
        action = ActionRequest(
            action_id=f"act_p11_{idx}",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product", id="prod_01"),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="probe inventory race"),
            amount=Money(100000, "INR"),
        )
        barrier.wait()
        res = orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_probe_50",
        )
        return idx, res.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(racer, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    successes = sum(1 for r in results if r[1] == ActionStatus.COMPLETED)
    failures = sum(1 for r in results if r[1] == ActionStatus.FAILED)
    final_inv = inv_repo.get("tenant_01", "var_probe_50")

    passed = (
        successes == 5
        and failures == 45
        and len(payment_provider._attempts) == 5
        and final_inv is not None
        and final_inv.reserved == 5
        and final_inv.effective_available == 0
    )

    return {
        "probe": "Vector 11: 50 Threads vs 5 Items",
        "passed": passed,
        "successes": successes,
        "failures": failures,
        "payment_attempts": len(payment_provider._attempts),
        "reserved": final_inv.reserved if final_inv else -1,
    }


# ===========================================================================
# Probe 3 & 4: Vector 10 — Multithreaded Approval Consumption TOCTOU Race
# ===========================================================================
def probe_vector_10_approval_consumption_race_condition() -> dict[str, Any]:
    """Test 50 concurrent threads racing to consume the exact same approved ActionRequest."""
    # Temporarily set fine switch interval to reveal thread preemption
    original_switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    try:
        orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, approval_repo = (
            setup_orchestrator()
        )

        variant_repo.save(
            "tenant_01",
            ProductVariant(
                variant_id="var_probe_appr",
                product_id="prod_01",
                sku="SKU-PROBE-APPR",
                name="Probe Approval Item",
                price=Money(800000, "INR"),
            ),
        )
        inv_repo.save(
            "tenant_01",
            Inventory(
                variant_id="var_probe_appr", tenant_id="tenant_01", available=100, reserved=0
            ),
        )

        action = ActionRequest(
            action_id="act_probe_appr_shared",
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

        r_review = orch.execute_purchase(
            action=action, tenant_id="tenant_01", variant_id="var_probe_appr"
        )
        assert r_review.approval is not None
        appr_id = r_review.approval.approval_id

        approval_svc = ApprovalService(approval_repo=approval_repo)
        approval_svc.grant_approval(
            approval_id=appr_id, approver_id="approver_audit", tenant_id="tenant_01"
        )

        num_threads = 50
        barrier = threading.Barrier(num_threads)
        winners: list[int] = []
        consumed_errors: list[int] = []

        def consume_racer(idx: int) -> None:
            barrier.wait()
            try:
                res = orch.execute_approved_purchase(
                    approval_id=appr_id,
                    action=action,
                    tenant_id="tenant_01",
                    variant_id="var_probe_appr",
                    idempotency_key=f"idem_probe_v10_{idx}",
                )
                if res.status == ActionStatus.COMPLETED:
                    winners.append(idx)
            except ApprovalAlreadyConsumedError:
                consumed_errors.append(idx)
            except Exception:
                pass

        threads = [threading.Thread(target=consume_racer, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Invariant: exactly 1 winner, exactly 1 payment attempt
        payments_captured = len(payment_provider._attempts)
        passed = len(winners) == 1 and payments_captured == 1

        return {
            "probe": "Vector 10: Approval Multi-Consumption Under Thread Contention",
            "passed": passed,
            "winners_count": len(winners),
            "payments_captured": payments_captured,
            "consumed_error_count": len(consumed_errors),
            "vulnerability_detected": len(winners) > 1,
        }
    finally:
        sys.setswitchinterval(original_switch)


def probe_vector_10_with_simulated_db_latency() -> dict[str, Any]:
    """Test approval consumption with simulated 5ms database lookup latency."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, approval_repo = (
        setup_orchestrator()
    )

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_probe_db",
            product_id="prod_01",
            sku="SKU-PROBE-DB",
            name="Probe DB Latency Item",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_probe_db", tenant_id="tenant_01", available=100, reserved=0),
    )

    action = ActionRequest(
        action_id="act_probe_db_shared",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="latency race"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    r_review = orch.execute_purchase(
        action=action, tenant_id="tenant_01", variant_id="var_probe_db"
    )
    assert r_review.approval is not None
    appr_id = r_review.approval.approval_id

    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(
        approval_id=appr_id, approver_id="approver_audit", tenant_id="tenant_01"
    )

    # Intercept approval_repo.atomic_consume to simulate 5ms DB IO latency
    orig_consume = approval_repo.atomic_consume

    def slow_consume(tenant_id: str, approval_id: str) -> Any:
        time.sleep(0.005)  # 5ms simulated DB roundtrip
        return orig_consume(tenant_id, approval_id)

    approval_repo.atomic_consume = slow_consume  # type: ignore[method-assign]

    num_threads = 5
    barrier = threading.Barrier(num_threads)
    winners: list[int] = []

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            res = orch.execute_approved_purchase(
                approval_id=appr_id,
                action=action,
                tenant_id="tenant_01",
                variant_id="var_probe_db",
                idempotency_key=f"idem_db_probe_{idx}",
            )
            if res.status == ActionStatus.COMPLETED:
                winners.append(idx)
        except Exception:
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    payments_captured = len(payment_provider._attempts)
    passed = len(winners) == 1 and payments_captured == 1

    return {
        "probe": "Vector 10: Approval Consumption with 5ms DB Latency (TOCTOU)",
        "passed": passed,
        "winners_count": len(winners),
        "payments_captured": payments_captured,
        "vulnerability_detected": len(winners) > 1,
    }


# ===========================================================================
# Probe 5: Vector 2 — Concurrent In-Flight Idempotency Payload Mutation
# ===========================================================================
def probe_vector_02_concurrent_inflight_idempotency_conflict() -> dict[str, Any]:
    """Test two concurrent in-flight requests submitting mutated amounts under the same key."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, _ = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_p02_1",
            product_id="prod_01",
            sku="SKU-02-1",
            name="Item 1",
            price=Money(100000, "INR"),
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_p02_2",
            product_id="prod_01",
            sku="SKU-02-2",
            name="Item 2",
            price=Money(150000, "INR"),
        ),
    )
    inv_repo.save("tenant_01", Inventory("var_p02_1", "tenant_01", 10, 0))
    inv_repo.save("tenant_01", Inventory("var_p02_2", "tenant_01", 10, 0))

    act1 = ActionRequest(
        action_id="act_inflight_1",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="inflight test 1"),
        amount=Money(100000, "INR"),
    )
    act2 = ActionRequest(
        action_id="act_inflight_2",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="inflight test 2"),
        amount=Money(150000, "INR"),
    )

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def t1() -> None:
        barrier.wait()
        try:
            res = orch.execute_purchase(
                action=act1,
                tenant_id="tenant_01",
                variant_id="var_p02_1",
                idempotency_key="key_shared_inflight",
            )
            outcomes["t1"] = res.status.value
        except IdempotencyConflictError:
            outcomes["t1"] = "CONFLICT"
        except Exception as e:
            outcomes["t1"] = type(e).__name__

    def t2() -> None:
        barrier.wait()
        try:
            res = orch.execute_purchase(
                action=act2,
                tenant_id="tenant_01",
                variant_id="var_p02_2",
                idempotency_key="key_shared_inflight",
            )
            outcomes["t2"] = res.status.value
        except IdempotencyConflictError:
            outcomes["t2"] = "CONFLICT"
        except Exception as e:
            outcomes["t2"] = type(e).__name__

    th1 = threading.Thread(target=t1)
    th2 = threading.Thread(target=t2)
    th1.start()
    th2.start()
    th1.join()
    th2.join()

    # Invariant: one must succeed, the other MUST raise IdempotencyConflictError (or fail safely)
    conflict_detected = "CONFLICT" in outcomes.values()
    double_completed = outcomes.get("t1") == "COMPLETED" and outcomes.get("t2") == "COMPLETED"

    return {
        "probe": "Vector 2: Concurrent In-Flight Idempotency Payload Mutation",
        "outcomes": outcomes,
        "payment_attempts": len(payment_provider._attempts),
        "conflict_detected": conflict_detected,
        "double_completed": double_completed,
        "vulnerability_detected": double_completed,
    }


def main() -> int:
    log("==================================================================")
    log("AGENTPAY EMPIRICAL CONCURRENCY & STRESS TEST HARNESS")
    log("==================================================================")

    p1 = probe_vector_11_inventory_contention_50_threads()
    log(f"[*] Probe 1 (Vector 11 - 50 Threads vs 5 Items): {'PASS' if p1['passed'] else 'FAIL'}")
    log(
        f"    Successes: {p1['successes']}, Failures: {p1['failures']}, Final Reserved: {p1['reserved']}"
    )

    p2 = probe_vector_10_approval_consumption_race_condition()
    log(
        f"[*] Probe 2 (Vector 10 - Concurrent Approval Consumption): {'PASS' if p2['passed'] else 'FAIL'}"
    )
    log(
        f"    Winners: {p2['winners_count']}, Payments: {p2['payments_captured']}, Vulnerability: {p2['vulnerability_detected']}"
    )

    p3 = probe_vector_10_with_simulated_db_latency()
    log(
        f"[*] Probe 3 (Vector 10 - Approval TOCTOU with DB Latency): {'PASS' if p3['passed'] else 'FAIL'}"
    )
    log(
        f"    Winners: {p3['winners_count']}, Payments: {p3['payments_captured']}, Vulnerability: {p3['vulnerability_detected']}"
    )

    p4 = probe_vector_02_concurrent_inflight_idempotency_conflict()
    log(
        f"[*] Probe 4 (Vector 2 - Concurrent In-Flight Idempotency Conflict): {'PASS' if not p4['vulnerability_detected'] else 'FAIL'}"
    )
    log(
        f"    Outcomes: {p4['outcomes']}, Payments: {p4['payment_attempts']}, Double Completed: {p4['double_completed']}"
    )

    log("==================================================================")
    failures = sum(1 for p in [p1, p2, p3] if not p["passed"]) + (
        1 if p4["vulnerability_detected"] else 0
    )
    if failures > 0:
        log(f"[!] RESULT: {failures} VULNERABILITY FINDINGS DETECTED -> REQUEST_CHANGES")
        return 1
    log("[+] RESULT: ALL INVARIANTS SATISFIED -> APPROVE")
    return 0


if __name__ == "__main__":
    sys.exit(main())

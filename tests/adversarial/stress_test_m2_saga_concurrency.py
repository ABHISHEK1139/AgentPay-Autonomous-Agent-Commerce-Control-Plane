"""Milestone 2 Empirical SAGA Compensation and Concurrency Stress Harness (§44, §20, §49, §177)."""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))
sys.path.insert(0, str(Path("apps/worker/src").resolve()))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import (
    Approval,
    ApprovalAlreadyConsumedError,
    ApprovalBinding,
    ApprovalStatus,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import (
    Inventory,
    ProductVariant,
)
from agentpay.domain.identity import Tenant
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    PaymentAttempt,
    PaymentStatus,
)
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)


def log(msg: str) -> None:
    """Output message to stdout."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ===========================================================================
# Probe 1: SAGA Rollback on Payment Creation Failure (Autonomous Flow)
# ===========================================================================
def probe_saga_payment_creation_failure_autonomous() -> dict[str, Any]:
    log("[->] Executing Probe 1 (Autonomous SAGA payment failure)...")
    orch, _, _, _, _, _, _variant_repo, inv_repo, payment_provider, audit_ledger, _ = (
        setup_orchestrator(tenant_id="tenant_01")
    )

    inv_before = inv_repo.get("tenant_01", "var_01")
    assert inv_before is not None
    assert inv_before.available == 10
    assert inv_before.reserved == 0

    def broken_create_payment(*args: Any, **kwargs: Any) -> PaymentAttempt:
        raise ConnectionResetError("PSP socket reset during payment intent creation")

    payment_provider.create_payment = broken_create_payment  # type: ignore[method-assign]

    action = ActionRequest(
        action_id="act_saga_auto_fail",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="saga autonomous payment failure"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    caught_exception = False
    try:
        orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_01",
            quantity=1,
        )
    except ConnectionResetError:
        caught_exception = True

    inv_after = inv_repo.get("tenant_01", "var_01")
    events = audit_ledger.get_events("tenant_01")
    cancel_events = [e for e in events if e.event_type == AuditEventType.ORDER_CANCELLED]

    passed = (
        caught_exception
        and inv_after is not None
        and inv_after.available == 10
        and inv_after.reserved == 0
        and len(cancel_events) == 1
        and "PSP socket reset" in str(cancel_events[0].data.get("reason", ""))
    )

    return {
        "probe": "Probe 1: SAGA Autonomous Payment Creation Failure",
        "passed": passed,
        "caught_exception": caught_exception,
        "available": inv_after.available if inv_after else -1,
        "reserved": inv_after.reserved if inv_after else -1,
        "cancel_events": len(cancel_events),
    }


# ===========================================================================
# Probe 2: SAGA Rollback on Payment Creation Failure (Approved Flow)
# ===========================================================================
def probe_saga_payment_creation_failure_approved() -> dict[str, Any]:
    log("[->] Executing Probe 2 (Approved SAGA payment failure)...")
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, audit_ledger, approval_repo = (
        setup_orchestrator(tenant_id="tenant_01")
    )

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_high_s2",
            product_id="prod_01",
            sku="SKU-HIGH-S2",
            name="Maxed Spec S2",
            price=Money(800000, "INR"),  # ₹8,000
        ),
    )
    inv_repo.save("tenant_01", Inventory("var_high_s2", "tenant_01", available=5, reserved=0))

    action = ActionRequest(
        action_id="act_saga_appr_fail",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="high value asset needing approval"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    res_review = orch.execute_purchase(
        action=action, tenant_id="tenant_01", variant_id="var_high_s2", quantity=1
    )
    assert res_review.status == ActionStatus.AWAITING_APPROVAL
    assert res_review.approval is not None
    appr_id = res_review.approval.approval_id

    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(
        approval_id=appr_id, approver_id="approver_s2", tenant_id="tenant_01"
    )

    def broken_create_payment(*args: Any, **kwargs: Any) -> PaymentAttempt:
        raise TimeoutError("PSP timeout during approved payment creation")

    payment_provider.create_payment = broken_create_payment  # type: ignore[method-assign]

    caught_exception = False
    try:
        orch.execute_approved_purchase(
            approval_id=appr_id,
            action=action,
            tenant_id="tenant_01",
            variant_id="var_high_s2",
            quantity=1,
        )
    except TimeoutError:
        caught_exception = True

    inv_after = inv_repo.get("tenant_01", "var_high_s2")
    events = audit_ledger.get_events("tenant_01")
    cancel_events = [e for e in events if e.event_type == AuditEventType.ORDER_CANCELLED]

    passed = (
        caught_exception
        and inv_after is not None
        and inv_after.available == 5
        and inv_after.reserved == 0
        and len(cancel_events) == 1
        and "PSP timeout" in str(cancel_events[0].data.get("reason", ""))
    )

    return {
        "probe": "Probe 2: SAGA Approved Payment Creation Failure",
        "passed": passed,
        "caught_exception": caught_exception,
        "available": inv_after.available if inv_after else -1,
        "reserved": inv_after.reserved if inv_after else -1,
        "cancel_events": len(cancel_events),
    }


# ===========================================================================
# Probe 3: Concurrent Partial Payment Failures & Inventory Balance
# ===========================================================================
def probe_concurrent_partial_payment_failures() -> dict[str, Any]:
    log("[->] Executing Probe 3 (Concurrent partial payment failures)...")
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, _ = setup_orchestrator(
        tenant_id="tenant_01"
    )

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_s3",
            product_id="prod_01",
            sku="SKU-S3",
            name="SAGA Item 3",
            price=Money(10000, "INR"),
        ),
    )
    inv_repo.save("tenant_01", Inventory("var_s3", "tenant_01", available=10, reserved=0))

    original_create = payment_provider.create_payment

    def conditional_create_payment(
        tenant_id: str, intent_id: str, amount: Money, idempotency_key: str | None = None
    ) -> PaymentAttempt:
        if "odd" in (idempotency_key or ""):
            raise RuntimeError("Simulated transient PSP network failure")
        return original_create(tenant_id, intent_id, amount, idempotency_key)

    payment_provider.create_payment = conditional_create_payment  # type: ignore[method-assign]

    num_threads = 20
    barrier = threading.Barrier(num_threads)
    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def racer(idx: int) -> None:
        tag = "odd" if idx % 2 == 1 else "even"
        action = ActionRequest(
            action_id=f"act_s3_{idx}",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="computers", id="prod_01"),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="concurrent saga stress"),
            amount=Money(10000, "INR"),
            authorization_mode=AuthorizationMode.AUTONOMOUS,
        )
        barrier.wait()
        try:
            res = orch.execute_purchase(
                action=action,
                tenant_id="tenant_01",
                variant_id="var_s3",
                quantity=1,
                idempotency_key=f"idem_s3_{idx}_{tag}",
            )
            with lock:
                results.append((idx, res.status.value))
        except Exception as e:
            with lock:
                results.append((idx, f"EXCEPTION:{type(e).__name__}"))

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    inv_final = inv_repo.get("tenant_01", "var_s3")
    assert inv_final is not None

    completed_count = sum(1 for _, st in results if st == "COMPLETED")
    exception_count = sum(1 for _, st in results if "EXCEPTION" in st)
    failed_count = sum(1 for _, st in results if st == "FAILED")

    reserved_matches_completed = inv_final.reserved == completed_count
    effective_available_correct = inv_final.effective_available == (10 - completed_count)

    passed = reserved_matches_completed and effective_available_correct and completed_count > 0

    return {
        "probe": "Probe 3: Concurrent Partial Payment Failures & Inventory Invariant",
        "passed": passed,
        "completed": completed_count,
        "exceptions": exception_count,
        "insufficient_inv_failures": failed_count,
        "final_available": inv_final.available,
        "final_reserved": inv_final.reserved,
        "effective_available_correct": effective_available_correct,
    }


# ===========================================================================
# Probe 4: SAGA Rollback on Post-Capture Order Persistence Failure (Autonomous)
# ===========================================================================
def probe_saga_order_persistence_failure_autonomous() -> dict[str, Any]:
    log("[->] Executing Probe 4 (Autonomous post-capture order persistence failure)...")
    orch, _, _, _, _, _, _variant_repo, inv_repo, payment_provider, audit_ledger, _ = (
        setup_orchestrator(tenant_id="tenant_01")
    )

    inv_before = inv_repo.get("tenant_01", "var_01")
    assert inv_before is not None
    assert inv_before.available == 10
    assert inv_before.reserved == 0

    def failing_save(*args: Any, **kwargs: Any) -> None:
        raise OSError("Disk quota exceeded on orders partition")

    assert orch._order_repo is not None
    orch._order_repo.save = failing_save  # type: ignore[method-assign]

    action = ActionRequest(
        action_id="act_saga_order_fail_auto",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="disk failure after payment capture"),
        amount=Money(129900, "INR"),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )

    caught_exception = False
    try:
        orch.execute_purchase(
            action=action,
            tenant_id="tenant_01",
            variant_id="var_01",
            quantity=1,
        )
    except OSError:
        caught_exception = True

    inv_after = inv_repo.get("tenant_01", "var_01")
    refunds = list(payment_provider._refunds.values())
    events = audit_ledger.get_events("tenant_01")
    cancel_events = [e for e in events if e.event_type == AuditEventType.ORDER_CANCELLED]

    passed = (
        caught_exception
        and inv_after is not None
        and inv_after.available == 10
        and inv_after.reserved == 0
        and len(refunds) == 1
        and refunds[0].amount.minor_units == 129900
        and len(cancel_events) == 1
        and "Disk quota exceeded" in str(cancel_events[0].data.get("reason", ""))
    )

    return {
        "probe": "Probe 4: SAGA Autonomous Post-Capture Order Persistence Failure",
        "passed": passed,
        "caught_exception": caught_exception,
        "available": inv_after.available if inv_after else -1,
        "reserved": inv_after.reserved if inv_after else -1,
        "refunds_count": len(refunds),
        "refund_amount": refunds[0].amount.minor_units if refunds else 0,
        "cancel_events": len(cancel_events),
    }


# ===========================================================================
# Probe 5: SAGA Rollback on Post-Capture Order Persistence Failure (Approved)
# ===========================================================================
def probe_saga_order_persistence_failure_approved() -> dict[str, Any]:
    log("[->] Executing Probe 5 (Approved post-capture order persistence failure)...")
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, audit_ledger, approval_repo = (
        setup_orchestrator(tenant_id="tenant_01")
    )

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_high_s5",
            product_id="prod_01",
            sku="SKU-HIGH-S5",
            name="Maxed Spec S5",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save("tenant_01", Inventory("var_high_s5", "tenant_01", available=4, reserved=0))

    action = ActionRequest(
        action_id="act_saga_order_fail_appr",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="approved high value order failure"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    res_review = orch.execute_purchase(
        action=action, tenant_id="tenant_01", variant_id="var_high_s5", quantity=1
    )
    assert res_review.status == ActionStatus.AWAITING_APPROVAL
    assert res_review.approval is not None
    appr_id = res_review.approval.approval_id

    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(
        approval_id=appr_id, approver_id="approver_s5", tenant_id="tenant_01"
    )

    def failing_save(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Database connection severed during order commit")

    assert orch._order_repo is not None
    orch._order_repo.save = failing_save  # type: ignore[method-assign]

    caught_exception = False
    try:
        orch.execute_approved_purchase(
            approval_id=appr_id,
            action=action,
            tenant_id="tenant_01",
            variant_id="var_high_s5",
            quantity=1,
        )
    except RuntimeError:
        caught_exception = True

    inv_after = inv_repo.get("tenant_01", "var_high_s5")
    refunds = list(payment_provider._refunds.values())
    events = audit_ledger.get_events("tenant_01")
    cancel_events = [e for e in events if e.event_type == AuditEventType.ORDER_CANCELLED]

    passed = (
        caught_exception
        and inv_after is not None
        and inv_after.available == 4
        and inv_after.reserved == 0
        and len(refunds) == 1
        and refunds[0].amount.minor_units == 800000
        and len(cancel_events) == 1
    )

    return {
        "probe": "Probe 5: SAGA Approved Post-Capture Order Persistence Failure",
        "passed": passed,
        "caught_exception": caught_exception,
        "available": inv_after.available if inv_after else -1,
        "reserved": inv_after.reserved if inv_after else -1,
        "refunds_count": len(refunds),
        "cancel_events": len(cancel_events),
    }


# ===========================================================================
# Probe 6: Concurrent Re-consumption on SqlAlchemyApprovalRepository
# ===========================================================================
def probe_sqlalchemy_approval_concurrent_atomic_consume() -> dict[str, Any]:
    log("[->] Executing Probe 6 (SqlAlchemyApprovalRepository concurrent atomic consume)...")
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_sqla_appr", name="SQLA Appr Tenant"))

    approval_repo = SqlAlchemyApprovalRepository(session_factory)
    now = datetime.now(UTC)

    binding = ApprovalBinding(
        action_id="act_sqla_race_01",
        amount=Money(350000, "USD"),
        merchant_id="merch_enterprise",
        resource_id="res_cluster_01",
        currency="USD",
        action_version=1,
        tenant_id="tenant_sqla_appr",
    )
    approval = Approval(
        approval_id="appr_sqla_shared_01",
        tenant_id="tenant_sqla_appr",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=now,
    )
    approval_repo.save("tenant_sqla_appr", approval)

    # Transition to APPROVED
    approved = approval.approve(approver_id="cfo_user_01")
    approval_repo.save("tenant_sqla_appr", approved)

    num_threads = 20
    barrier = threading.Barrier(num_threads)
    winners: list[int] = []
    consumed_errors: list[int] = []
    other_exceptions: list[tuple[int, str]] = []
    lock = threading.Lock()

    def racer(idx: int) -> None:
        barrier.wait()
        try:
            approval_repo.atomic_consume("tenant_sqla_appr", "appr_sqla_shared_01")
            with lock:
                winners.append(idx)
        except ApprovalAlreadyConsumedError:
            with lock:
                consumed_errors.append(idx)
        except Exception as exc:
            with lock:
                other_exceptions.append((idx, f"{type(exc).__name__}: {exc}"))

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_approval = approval_repo.get("tenant_sqla_appr", "appr_sqla_shared_01")
    assert final_approval is not None

    passed = (
        len(winners) == 1
        and len(consumed_errors) == (num_threads - 1)
        and len(other_exceptions) == 0
        and final_approval.status == ApprovalStatus.CONSUMED
    )

    return {
        "probe": "Probe 6a: SqlAlchemyApprovalRepository Concurrent Atomic Consume",
        "passed": passed,
        "winners": len(winners),
        "consumed_errors": len(consumed_errors),
        "other_exceptions": len(other_exceptions),
        "final_db_status": final_approval.status.value,
        "vulnerability_double_consume": len(winners) > 1,
    }


def probe_orchestrator_approved_purchase_concurrency_with_sqla() -> dict[str, Any]:
    log(
        "[->] Executing Probe 6b (PurchaseOrchestrator approved purchase concurrency under SqlAlchemyApprovalRepository)..."
    )
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _audit_ledger, _ = (
        setup_orchestrator(tenant_id="tenant_01")
    )
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tenant_repo_sqla = SqlAlchemyTenantRepository(session_factory)
    tenant_repo_sqla.save(Tenant(tenant_id="tenant_01", name="T1"))
    approval_repo_sqla = SqlAlchemyApprovalRepository(session_factory)
    orch._approval_repo = approval_repo_sqla
    orch._approval_service = ApprovalService(approval_repo=approval_repo_sqla)
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_sqla_test",
            product_id="prod_01",
            sku="SKU-SQLA",
            name="SQLA Item",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save("tenant_01", Inventory("var_sqla_test", "tenant_01", available=100, reserved=0))
    now = datetime.now(UTC)
    binding = ApprovalBinding(
        action_id="act_shared_sqla",
        amount=Money(800000, "INR"),
        merchant_id="prod_01",
        resource_id="prod_01",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
    )
    approval = Approval(
        approval_id="appr_shared_sqla",
        tenant_id="tenant_01",
        binding=binding,
        status=ApprovalStatus.APPROVED,
        requested_at=now,
        approver_id="cfo",
    )
    approval_repo_sqla.save("tenant_01", approval)
    act = ActionRequest(
        action_id="act_shared_sqla",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="computers", id="prod_01"),
        requested_at=now,
        intent=Intent(purpose="test"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    winners: list[int] = []
    consumed_errors: list[int] = []
    other_errors: list[tuple[int, str]] = []
    lock = threading.Lock()

    def racer(idx: int) -> None:
        barrier.wait()
        try:
            res = orch.execute_approved_purchase(
                approval_id="appr_shared_sqla",
                action=act,
                tenant_id="tenant_01",
                variant_id="var_sqla_test",
                quantity=1,
                idempotency_key=f"idem_sqla_race_{idx}",
            )
            if res.status == ActionStatus.COMPLETED:
                with lock:
                    winners.append(idx)
        except ApprovalAlreadyConsumedError:
            with lock:
                consumed_errors.append(idx)
        except Exception as e:
            with lock:
                other_errors.append((idx, type(e).__name__))

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    passed = len(winners) == 1 and len(payment_provider._attempts) == 1

    return {
        "probe": "Probe 6b: PurchaseOrchestrator Approved Purchase Under SqlAlchemyApprovalRepository",
        "passed": passed,
        "completed_purchases": len(winners),
        "consumed_errors": len(consumed_errors),
        "payments_captured": len(payment_provider._attempts),
        "vulnerability_double_purchase": len(winners) > 1,
    }


# ===========================================================================
# Probe 7: PaymentReconciler.reconcile_timeout Integration
# ===========================================================================
def probe_payment_reconciler_timeout_resolution() -> dict[str, Any]:
    log("[->] Executing Probe 7 (PaymentReconciler.reconcile_timeout resolution)...")
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator(tenant_id="tenant_rec_1")
    reconciler = PaymentReconciler(
        payment_repo=orch._payment_repo,  # type: ignore[arg-type]
        payment_provider=payment_provider,
        order_repo=orch._order_repo,  # type: ignore[arg-type]
        audit_ledger=orch._audit_ledger,  # type: ignore[arg-type]
    )

    now = datetime.now(UTC)
    unknown_attempt = PaymentAttempt(
        attempt_id="att_unknown_01",
        intent_id="intent_01",
        tenant_id="tenant_rec_1",
        amount=Money(75000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="idem_unknown_01",
        provider="mock",
        created_at=now,
        updated_at=now,
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_rec_1", unknown_attempt)

    provider_attempt = PaymentAttempt(
        attempt_id="att_unknown_01",
        intent_id="intent_01",
        tenant_id="tenant_rec_1",
        amount=Money(75000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="idem_unknown_01",
        provider="mock",
        created_at=now,
        updated_at=now,
    )
    payment_provider._attempts[("tenant_rec_1", "att_unknown_01")] = provider_attempt

    resolved = reconciler.reconcile_timeout(unknown_attempt)

    passed = resolved.status == PaymentStatus.CAPTURED and resolved.attempt_id == "att_unknown_01"

    return {
        "probe": "Probe 7: PaymentReconciler.reconcile_timeout Resolution",
        "passed": passed,
        "resolved_status": resolved.status.value,
    }


# ===========================================================================
# Probe 8: BackgroundWorker Expiring Stale Approvals on Frozen Dataclass
# ===========================================================================
def probe_background_worker_stale_approval_expiration() -> dict[str, Any]:
    log("[->] Executing Probe 8 (BackgroundWorker immutable approval sweep)...")
    from agentpay_worker.worker import BackgroundWorker as StandaloneWorker

    from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository

    approval_repo = InMemoryApprovalRepository()
    worker = StandaloneWorker(
        outbox_repo=None,  # type: ignore[arg-type]
        event_bus=None,  # type: ignore[arg-type]
        approval_repo=approval_repo,
        payment_reconciler=None,  # type: ignore[arg-type]
        approval_ttl_seconds=3600,
    )

    now = datetime.now(UTC)
    stale_time = now - timedelta(hours=2)

    stale_app = Approval(
        approval_id="appr_stale_01",
        tenant_id="tenant_worker_1",
        binding=ApprovalBinding(
            action_id="act_stale",
            amount=Money(10000, "INR"),
            merchant_id="m1",
            resource_id="r1",
            currency="INR",
            action_version=1,
            tenant_id="tenant_worker_1",
        ),
        status=ApprovalStatus.PENDING,
        requested_at=stale_time,
    )
    approval_repo.save("tenant_worker_1", stale_app)

    expired_count = worker._expire_stale_approvals()

    retrieved = approval_repo.get("tenant_worker_1", "appr_stale_01")
    passed = (
        expired_count == 1
        and retrieved is not None
        and retrieved.status == ApprovalStatus.REJECTED
        and retrieved.approver_id == "system_worker"
        and "past TTL" in (retrieved.reason or "")
    )

    return {
        "probe": "Probe 8: BackgroundWorker Immutable Approval Sweep",
        "passed": passed,
        "expired_count": expired_count,
        "final_status": retrieved.status.value if retrieved else "None",
    }


def main() -> int:
    log("==================================================================")
    log("MILENSTONE 2 EMPIRICAL SAGA & CONCURRENCY CHALLENGE HARNESS")
    log("==================================================================")

    probe_funcs = [
        probe_saga_payment_creation_failure_autonomous,
        probe_saga_payment_creation_failure_approved,
        probe_concurrent_partial_payment_failures,
        probe_saga_order_persistence_failure_autonomous,
        probe_saga_order_persistence_failure_approved,
        probe_sqlalchemy_approval_concurrent_atomic_consume,
        probe_orchestrator_approved_purchase_concurrency_with_sqla,
        probe_payment_reconciler_timeout_resolution,
        probe_background_worker_stale_approval_expiration,
    ]

    probes = []
    failures = 0
    for fn in probe_funcs:
        p = fn()
        probes.append(p)
        status_str = "PASS" if p["passed"] else "FAIL"
        log(f"[*] {p['probe']}: {status_str}")
        for k, v in p.items():
            if k not in ("probe", "passed"):
                log(f"    {k}: {v}")
        if not p["passed"]:
            failures += 1

    log("==================================================================")
    if failures > 0:
        log(f"[!] RESULT: {failures} PROBES FAILED -> REQUEST_CHANGES")
        return 1
    log("[+] RESULT: ALL 8 PROBES PASSED -> APPROVE")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Milestone 4 Empirical Adversarial Stress Harness for AP-006: Approval Principal & Tenant Binding (§20, §49, §50, §66, §177).

Exhaustively probes:
1. Probe 1: Cross-Principal Replay Rejection in Same Tenant
2. Probe 2: Cross-Tenant Replay Rejection
3. Probe 3: Tampering with any of the 8 bound parameters
4. Probe 4: Multithreaded Concurrent Atomic Single-Use Consumption (20 threads)
5. Probe 5: Lifecycle State Guards (expired, revoked, cancelled, rejected cannot execute)
6. Probe 6: Catalog Price & Stock Drift Re-validation at Execution Time (§20)
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.application.services.approval_service import ApprovalService
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
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Tenant,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import PolicyDecision, SpendingLimits
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)


def log(msg: str) -> None:
    """Output message to stdout with flush."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _make_action(
    action_id: str = "act_m4_01",
    principal_id: str = "user_01",
    agent_id: str = "agent_01",
    tenant_id: str = "tenant_01",
    resource_id: str = "prod_01",
    amount_minor: int = 129900,
    currency: str = "INR",
    version: int = 1,
) -> ActionRequest:
    return ActionRequest(
        action_id=action_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        agent_id=agent_id,
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id=resource_id),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="procurement"),
        amount=Money(amount_minor, currency),
        authorization_mode=AuthorizationMode.SUPERVISED,
        version=version,
    )


def probe_cross_principal_replay_rejection() -> dict[str, Any]:
    """Probe 1: User Bob cannot execute User Alice's approval within the same tenant (§20, AP-006)."""
    violations: list[str] = []
    orch, _, agent_repo, perms_repo, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    # Seed agents for Alice and Bob
    now = datetime.now(UTC)
    agent_repo.save(
        "tenant_01",
        Agent(
            agent_id="agent_alice",
            tenant_id="tenant_01",
            principal_id="user_alice",
            issuer="https://auth.agentpay.local",
            capabilities=("catalog.search", "payment.request", "checkout.prepare"),
            scopes=("commerce:read", "commerce:write"),
            created_at=now,
            expires_at=now + timedelta(days=30),
            status=AgentStatus.ACTIVE,
        ),
    )
    perms_repo.save(
        "tenant_01",
        AgentPermissions(
            agent_id="agent_alice",
            tenant_id="tenant_01",
            capabilities=("catalog.search", "payment.request", "checkout.prepare"),
            limits=SpendingLimits(
                per_transaction_minor=2000000,
                daily_minor=5000000,
                requires_approval_above_minor=200000,
            ),
            allowed_categories=("electronics", "computers"),
            requires_approval_above_minor=200000,
        ),
    )
    agent_repo.save(
        "tenant_01",
        Agent(
            agent_id="agent_bob",
            tenant_id="tenant_01",
            principal_id="user_bob",
            issuer="https://auth.agentpay.local",
            capabilities=("catalog.search", "payment.request", "checkout.prepare"),
            scopes=("commerce:read", "commerce:write"),
            created_at=now,
            expires_at=now + timedelta(days=30),
            status=AgentStatus.ACTIVE,
        ),
    )
    perms_repo.save(
        "tenant_01",
        AgentPermissions(
            agent_id="agent_bob",
            tenant_id="tenant_01",
            capabilities=("catalog.search", "payment.request", "checkout.prepare"),
            limits=SpendingLimits(
                per_transaction_minor=2000000,
                daily_minor=5000000,
                requires_approval_above_minor=200000,
            ),
            allowed_categories=("electronics", "computers"),
            requires_approval_above_minor=200000,
        ),
    )

    # 1. Create and approve action for Alice
    alice_action = _make_action(
        action_id="act_alice_p1",
        principal_id="user_alice",
        agent_id="agent_alice",
        tenant_id="tenant_01",
        resource_id="prod_01",
        amount_minor=129900,
    )
    approval = orch._approval_service.create_approval(alice_action, tenant_id="tenant_01")
    orch._approval_service.grant_approval(
        approval.approval_id, approver_id="manager_01", tenant_id="tenant_01"
    )

    # 2. Bob attempts to execute Alice's approval directly via orchestrator
    bob_action = _make_action(
        action_id="act_alice_p1",
        principal_id="user_bob",
        agent_id="agent_bob",
        tenant_id="tenant_01",
        resource_id="prod_01",
        amount_minor=129900,
    )
    try:
        orch.execute_approved_purchase(
            approval_id=approval.approval_id,
            action=bob_action,
            tenant_id="tenant_01",
            variant_id="var_01",
            quantity=1,
        )
        violations.append("Cross-principal execution did not raise ApprovalBindingMismatchError")
    except ApprovalBindingMismatchError as err:
        if "user_alice" not in str(err) or "user_bob" not in str(err):
            violations.append(f"Unexpected error message on principal mismatch: {err}")
    except Exception as exc:
        violations.append(f"Unexpected exception type: {type(exc).__name__}: {exc}")

    # 3. Verify Alice's approval was NOT consumed by Bob's rogue attempt
    appr_after = orch._approval_repo.get("tenant_01", approval.approval_id)
    if appr_after is None or appr_after.status != ApprovalStatus.APPROVED:
        violations.append(
            f"Approval was corrupted/burned by rejected Bob probe; status={getattr(appr_after, 'status', None)}"
        )

    # 4. Bob attempts to execute Alice's approval via HTTP API
    status, body = client.post(
        f"/api/v1/approvals/{approval.approval_id}/execute",
        json_body={
            "action_id": "act_alice_p1",
            "principal_id": "user_bob",
            "agent_id": "agent_bob",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 129900,
            "currency": "INR",
        },
        headers={"x-tenant-id": "tenant_01"},
    )
    if status != 422:
        violations.append(f"Expected HTTP 422 for cross-principal execute, got {status}: {body}")

    # 5. Legitimate Alice execution succeeds
    try:
        res = orch.execute_approved_purchase(
            approval_id=approval.approval_id,
            action=alice_action,
            tenant_id="tenant_01",
            variant_id="var_01",
            quantity=1,
        )
        if res.status != ActionStatus.COMPLETED:
            violations.append(
                f"Legitimate Alice execution did not complete: {res.status} ({res.rejection_reason})"
            )
    except Exception as exc:
        violations.append(f"Legitimate Alice execution raised unexpected exception: {exc}")

    return {
        "probe": "probe_cross_principal_replay_rejection",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_cross_tenant_replay_rejection() -> dict[str, Any]:
    """Probe 2: Tenant B cannot execute or access Tenant A's approval (§49, §50, AP-006)."""
    violations: list[str] = []
    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    # Create and grant approval in tenant_01
    action = _make_action(action_id="act_t1", tenant_id="tenant_01")
    approval = orch._approval_service.create_approval(action, tenant_id="tenant_01")
    orch._approval_service.grant_approval(
        approval.approval_id, approver_id="mgr_01", tenant_id="tenant_01"
    )

    # 1. Direct cross-tenant orchestrator execution
    action_t2 = _make_action(action_id="act_t1", tenant_id="tenant_02")
    try:
        orch.execute_approved_purchase(
            approval_id=approval.approval_id,
            action=action_t2,
            tenant_id="tenant_02",
            variant_id="var_01",
            quantity=1,
        )
        violations.append("Cross-tenant execution did not fail closed")
    except (CrossTenantAccessError, ApprovalBindingMismatchError, Exception):
        pass  # Expected fail-closed behavior

    # 2. HTTP API cross-tenant execution attempt
    status, _ = client.post(
        f"/api/v1/approvals/{approval.approval_id}/execute",
        json_body={"action_id": "act_t1", "amount_minor": 129900, "currency": "INR"},
        headers={"x-tenant-id": "tenant_02"},
    )
    if status not in (403, 404, 422):
        violations.append(f"Expected HTTP 403/404/422 for cross-tenant API access, got {status}")

    # 3. HTTP API cross-tenant detail lookup attempt
    status_get, _ = client.get(
        f"/api/v1/approvals/{approval.approval_id}",
        headers={"x-tenant-id": "tenant_02"},
    )
    if status_get != 404:
        violations.append(
            f"Cross-tenant detail inspection leaked across boundary: status={status_get}"
        )

    return {
        "probe": "probe_cross_tenant_replay_rejection",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_8_parameter_tampering_defense() -> dict[str, Any]:
    """Probe 3: Tampering with any of the 8 bound parameters strictly fails closed (§20)."""
    violations: list[str] = []
    base_binding = ApprovalBinding(
        action_id="act_base",
        amount=Money(50000, "INR"),
        merchant_id="merch_01",
        resource_id="prod_01",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
        principal_id="user_alice",
    )

    tamper_cases: list[tuple[str, dict[str, Any], str]] = [
        (
            "action_id",
            {"action_id": "act_tampered"},
            "Action ID mismatch",
        ),
        (
            "amount",
            {"amount": Money(50001, "INR")},
            "Amount mismatch",
        ),
        (
            "merchant_id",
            {"merchant_id": "merch_other"},
            "Merchant mismatch",
        ),
        (
            "resource_id",
            {"resource_id": "prod_other"},
            "Resource mismatch",
        ),
        (
            "currency",
            {"currency": "USD", "amount": Money(50000, "USD")},
            "Currency mismatch",
        ),
        (
            "action_version",
            {"action_version": 2},
            "Action version mismatch",
        ),
        (
            "principal_id",
            {"principal_id": "user_mallory"},
            "Principal mismatch",
        ),
        (
            "tenant_id",
            {"tenant_id": "tenant_evil"},
            "Tenant mismatch",
        ),
    ]

    for field_name, overrides, expected_err in tamper_cases:
        kwargs: dict[str, Any] = {
            "action_id": base_binding.action_id,
            "amount": base_binding.amount,
            "merchant_id": base_binding.merchant_id,
            "resource_id": base_binding.resource_id,
            "currency": base_binding.currency,
            "action_version": base_binding.action_version,
            "tenant_id": base_binding.tenant_id,
            "principal_id": base_binding.principal_id,
        }
        kwargs.update(overrides)

        # 1. matches must return False
        if base_binding.matches(**kwargs) is not False:
            violations.append(f"Tampered {field_name} returned True on matches()")

        # 2. assert_exact_match must raise ApprovalBindingMismatchError with specific message
        try:
            base_binding.assert_exact_match(**kwargs)
            violations.append(f"Tampered {field_name} did not raise ApprovalBindingMismatchError")
        except ApprovalBindingMismatchError as err:
            if expected_err not in str(err):
                violations.append(
                    f"Tampered {field_name} raised unexpected error message: '{err}', expected '{expected_err}'"
                )
        except Exception as exc:
            violations.append(f"Tampered {field_name} raised unexpected exception: {exc}")

    return {
        "probe": "probe_8_parameter_tampering_defense",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_concurrent_atomic_single_use_consumption() -> dict[str, Any]:
    """Probe 4: 20 concurrent threads race to consume 1 approval -> exactly 1 succeeds (§20, §177 Vector 10)."""
    violations: list[str] = []

    # Test A: In-Memory repository atomic consume race
    in_mem_repo = InMemoryApprovalRepository()
    in_mem_service = ApprovalService(in_mem_repo)
    act = _make_action(action_id="act_race_mem")
    appr_mem = in_mem_service.create_approval(act, tenant_id="tenant_01")
    in_mem_service.grant_approval(appr_mem.approval_id, "mgr", "tenant_01")

    success_mem = 0
    conflict_mem = 0
    lock_mem = threading.Lock()

    def consume_mem() -> None:
        nonlocal success_mem, conflict_mem
        try:
            in_mem_repo.atomic_consume("tenant_01", appr_mem.approval_id)
            with lock_mem:
                success_mem += 1
        except ApprovalAlreadyConsumedError:
            with lock_mem:
                conflict_mem += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(consume_mem) for _ in range(20)]
        concurrent.futures.wait(futures)

    if success_mem != 1:
        violations.append(
            f"In-memory race failed: expected exactly 1 winner, got {success_mem} (conflicts: {conflict_mem})"
        )
    if conflict_mem != 19:
        violations.append(f"In-memory race failed: expected 19 conflicts, got {conflict_mem}")

    # Test B: SQLAlchemy relational repository atomic consume race
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_sqla", name="SQLA Org"))
    sqla_repo = SqlAlchemyApprovalRepository(session_factory)
    sqla_service = ApprovalService(sqla_repo)

    act_sqla = _make_action(action_id="act_race_sqla", tenant_id="tenant_sqla")
    appr_sqla = sqla_service.create_approval(act_sqla, tenant_id="tenant_sqla")
    sqla_service.grant_approval(appr_sqla.approval_id, "mgr", "tenant_sqla")

    success_sqla = 0
    conflict_sqla = 0
    lock_sqla = threading.Lock()

    def consume_sqla() -> None:
        nonlocal success_sqla, conflict_sqla
        try:
            sqla_repo.atomic_consume("tenant_sqla", appr_sqla.approval_id)
            with lock_sqla:
                success_sqla += 1
        except ApprovalAlreadyConsumedError:
            with lock_sqla:
                conflict_sqla += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(consume_sqla) for _ in range(20)]
        concurrent.futures.wait(futures)

    if success_sqla != 1:
        violations.append(
            f"SQLAlchemy race failed: expected exactly 1 winner, got {success_sqla} (conflicts: {conflict_sqla})"
        )
    if conflict_sqla != 19:
        violations.append(f"SQLAlchemy race failed: expected 19 conflicts, got {conflict_sqla}")

    engine.dispose()

    return {
        "probe": "probe_concurrent_atomic_single_use_consumption",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_lifecycle_state_guards() -> dict[str, Any]:
    """Probe 5: Approvals in PENDING, REJECTED, EXPIRED, CANCELLED, or CONSUMED cannot execute (§20)."""
    violations: list[str] = []
    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_01")
    service = orch._approval_service
    repo = orch._approval_repo
    action = _make_action(action_id="act_lifecycle")

    # 1. PENDING cannot execute
    appr_pending = service.create_approval(action, tenant_id="tenant_01")
    try:
        orch.execute_approved_purchase(appr_pending.approval_id, action, "tenant_01", "var_01", 1)
        violations.append("PENDING approval executed without being granted")
    except ApprovalBindingMismatchError:
        pass  # Expected

    # 2. REJECTED cannot execute
    appr_rejected = service.create_approval(
        _make_action(action_id="act_rej"), tenant_id="tenant_01"
    )
    service.reject_approval(
        appr_rejected.approval_id, "mgr", "Too expensive", tenant_id="tenant_01"
    )
    try:
        orch.execute_approved_purchase(
            appr_rejected.approval_id,
            _make_action(action_id="act_rej"),
            "tenant_01",
            "var_01",
            1,
        )
        violations.append("REJECTED approval executed successfully")
    except ApprovalBindingMismatchError:
        pass  # Expected

    # 3. CANCELLED cannot execute
    appr_cancelled = service.create_approval(
        _make_action(action_id="act_canc"), tenant_id="tenant_01"
    )
    canc_entity = Approval(
        approval_id=appr_cancelled.approval_id,
        tenant_id="tenant_01",
        binding=appr_cancelled.binding,
        status=ApprovalStatus.CANCELLED,
    )
    repo.save("tenant_01", canc_entity)
    try:
        orch.execute_approved_purchase(
            appr_cancelled.approval_id,
            _make_action(action_id="act_canc"),
            "tenant_01",
            "var_01",
            1,
        )
        violations.append("CANCELLED approval executed successfully")
    except ApprovalBindingMismatchError:
        pass  # Expected

    # 4. EXPIRED cannot execute
    appr_expired = service.create_approval(_make_action(action_id="act_exp"), tenant_id="tenant_01")
    exp_entity = Approval(
        approval_id=appr_expired.approval_id,
        tenant_id="tenant_01",
        binding=appr_expired.binding,
        status=ApprovalStatus.EXPIRED,
    )
    repo.save("tenant_01", exp_entity)
    try:
        orch.execute_approved_purchase(
            appr_expired.approval_id,
            _make_action(action_id="act_exp"),
            "tenant_01",
            "var_01",
            1,
        )
        violations.append("EXPIRED approval executed successfully")
    except ApprovalBindingMismatchError:
        pass  # Expected

    # 5. CONSUMED cannot execute twice
    appr_valid = service.create_approval(_make_action(action_id="act_ok"), tenant_id="tenant_01")
    service.grant_approval(appr_valid.approval_id, "mgr", "tenant_01")
    # First execution succeeds
    res1 = orch.execute_approved_purchase(
        appr_valid.approval_id, _make_action(action_id="act_ok"), "tenant_01", "var_01", 1
    )
    if res1.status != ActionStatus.COMPLETED:
        violations.append(f"Initial execution failed: {res1.status} ({res1.rejection_reason})")
    # Second execution raises ApprovalAlreadyConsumedError
    try:
        orch.execute_approved_purchase(
            appr_valid.approval_id, _make_action(action_id="act_ok"), "tenant_01", "var_01", 1
        )
        violations.append("CONSUMED approval executed twice without conflict error")
    except ApprovalAlreadyConsumedError:
        pass  # Expected

    return {
        "probe": "probe_lifecycle_state_guards",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_catalog_drift_revalidation_at_execution() -> dict[str, Any]:
    """Probe 6: Live Catalog Price & Stock Drift Re-validation at Execution Time (§20, AP-006)."""
    violations: list[str] = []
    orch, _, _, _, merchant_repo, product_repo, variant_repo, inventory_repo, _, _, _ = (
        setup_orchestrator(tenant_id="tenant_01")
    )

    # Seed merchant, product, variant, inventory
    merchant_repo.save(
        "tenant_01",
        Merchant(
            merchant_id="merch_drift",
            tenant_id="tenant_01",
            name="Drift Test Merchant",
            status=MerchantStatus.ACTIVE,
            allowed_agent_purchase=True,
        ),
    )
    product_repo.save(
        "tenant_01",
        Product(
            product_id="prod_drift",
            tenant_id="tenant_01",
            merchant_id="merch_drift",
            name="Drift Item",
            description="Item for drift testing",
            category="computers",
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_drift",
            product_id="prod_drift",
            sku="SKU-DRIFT-01",
            name="Drift Variant",
            price=Money(50000, "INR"),
        ),
    )
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_drift",
            tenant_id="tenant_01",
            available=10,
            reserved=0,
        ),
    )

    action = ActionRequest(
        action_id="act_drift_test",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_drift"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="procurement"),
        amount=Money(50000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
        version=1,
    )
    approval = orch._approval_service.create_approval(action, tenant_id="tenant_01")
    orch._approval_service.grant_approval(
        approval.approval_id, approver_id="mgr", tenant_id="tenant_01"
    )

    # Scenario A: Price drifts in catalog from 50000 to 60000 between approval and execution
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_drift",
            product_id="prod_drift",
            sku="SKU-DRIFT-01",
            name="Drift Variant",
            price=Money(60000, "INR"),  # Price changed!
        ),
    )
    res_price_drift = orch.execute_approved_purchase(
        approval_id=approval.approval_id,
        action=action,
        tenant_id="tenant_01",
        variant_id="var_drift",
        quantity=1,
    )
    if res_price_drift.decision != PolicyDecision.DENY:
        violations.append(
            f"Price drift was not denied: decision={res_price_drift.decision}, status={res_price_drift.status}"
        )
    if (
        not res_price_drift.rejection_reason
        or "conflict" not in res_price_drift.rejection_reason.lower()
    ):
        violations.append(
            f"Unexpected rejection reason for price drift: {res_price_drift.rejection_reason}"
        )

    # Reset price back to 50000, create a new fresh approval for stock drift probe
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_drift",
            product_id="prod_drift",
            sku="SKU-DRIFT-01",
            name="Drift Variant",
            price=Money(50000, "INR"),
        ),
    )
    action_stock = _make_action(
        action_id="act_stock_drift", resource_id="prod_drift", amount_minor=50000
    )
    appr_stock = orch._approval_service.create_approval(action_stock, tenant_id="tenant_01")
    orch._approval_service.grant_approval(appr_stock.approval_id, "mgr", "tenant_01")

    # Scenario B: Inventory drops to 0 before execution
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_drift",
            tenant_id="tenant_01",
            available=0,
            reserved=0,
        ),
    )
    res_stock_drift = orch.execute_approved_purchase(
        approval_id=appr_stock.approval_id,
        action=action_stock,
        tenant_id="tenant_01",
        variant_id="var_drift",
        quantity=1,
    )
    if res_stock_drift.decision != PolicyDecision.DENY:
        violations.append(
            f"Stock exhaustion drift was not denied: decision={res_stock_drift.decision}"
        )

    # Scenario C: Merchant deactivated before execution
    inventory_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_drift",
            tenant_id="tenant_01",
            available=10,
            reserved=0,
        ),
    )
    merchant_repo.save(
        "tenant_01",
        Merchant(
            merchant_id="merch_drift",
            tenant_id="tenant_01",
            name="Drift Test Merchant",
            status=MerchantStatus.SUSPENDED,  # Suspended!
            allowed_agent_purchase=False,
        ),
    )
    action_merch = _make_action(
        action_id="act_merch_drift", resource_id="prod_drift", amount_minor=50000
    )
    appr_merch = orch._approval_service.create_approval(action_merch, tenant_id="tenant_01")
    orch._approval_service.grant_approval(appr_merch.approval_id, "mgr", "tenant_01")

    res_merch = orch.execute_approved_purchase(
        approval_id=appr_merch.approval_id,
        action=action_merch,
        tenant_id="tenant_01",
        variant_id="var_drift",
        quantity=1,
    )
    if res_merch.decision != PolicyDecision.DENY:
        violations.append(f"Suspended merchant was not denied: decision={res_merch.decision}")

    return {
        "probe": "probe_catalog_drift_revalidation_at_execution",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def main() -> int:
    log("=" * 72)
    log("AGENTPAY MILESTONE 4: APPROVAL PRINCIPAL & TENANT BINDING ADVERSARIAL HARNESS")
    log("=" * 72)

    probes = [
        ("probe_cross_principal_replay_rejection", probe_cross_principal_replay_rejection),
        ("probe_cross_tenant_replay_rejection", probe_cross_tenant_replay_rejection),
        ("probe_8_parameter_tampering_defense", probe_8_parameter_tampering_defense),
        (
            "probe_concurrent_atomic_single_use_consumption",
            probe_concurrent_atomic_single_use_consumption,
        ),
        ("probe_lifecycle_state_guards", probe_lifecycle_state_guards),
        (
            "probe_catalog_drift_revalidation_at_execution",
            probe_catalog_drift_revalidation_at_execution,
        ),
    ]

    all_passed = True
    for name, fn in probes:
        res = fn()
        if res.get("passed"):
            log(f"[*] {name}: PASS")
        else:
            log(f"[!] {name}: FAIL -> {res.get('violations')}")
            all_passed = False

    log("=" * 72)
    if all_passed:
        log("[+] VERDICT: ALL MILESTONE 4 ADVERSARIAL PROBES SATISFIED -> PASS")
        return 0
    else:
        log("[-] VERDICT: VULNERABILITIES DETECTED -> FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())

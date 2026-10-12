"""Empirical Challenger Edge-Case Harness for Milestone 4 (AP-006: Approval Principal & Tenant Binding).

Adversarially tests:
1. Immutability & Slots Protection (FrozenInstanceError, AttributeError on binding and approval)
2. Whitespace, Malformed, and Cross-Case Principal & Tenant Tampering
3. Cross-Tenant Replay with Identical Namespaces (Alice in Tenant A vs Alice in Tenant B)
4. Double-Spend HTTP Replay via API (First -> 200, Second -> 409 APPROVAL_ALREADY_CONSUMED)
5. Currency Mismatch and Non-Money Amount Type Injection
6. Concurrent 30-Thread Purchase Execution Race against Persistent DB Repository
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from dataclasses import FrozenInstanceError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

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
    ApprovalAlreadyConsumedError,
    ApprovalBinding,
    ApprovalStatus,
)
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidApprovalError,
    InvalidCommerceError,
)
from agentpay.domain.identity import (
    Tenant,
)
from agentpay.domain.money import Money
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _make_action(
    action_id: str = "act_challenger_01",
    principal_id: str = "user_alice",
    agent_id: str = "agent_alice",
    tenant_id: str = "tenant_alpha",
    resource_id: str = "prod_01",
    amount_minor: int = 50000,
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


def test_immutability_and_slot_protection() -> dict[str, Any]:
    """Verify that ApprovalBinding and Approval entities cannot be mutated or extended."""
    violations: list[str] = []
    binding = ApprovalBinding(
        action_id="act_01",
        amount=Money(1000, "INR"),
        merchant_id="merch_01",
        resource_id="prod_01",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
        principal_id="user_alice",
    )

    # 1. Attribute modification must raise FrozenInstanceError
    try:
        binding.principal_id = "user_bob"  # type: ignore[misc]
        violations.append("ApprovalBinding permitted direct attribute mutation")
    except (FrozenInstanceError, AttributeError):
        pass  # Expected

    # 2. Dynamic attribute assignment must fail (slots=True)
    try:
        binding.bypassed = True
        violations.append("ApprovalBinding permitted dynamic attribute addition")
    except (FrozenInstanceError, AttributeError, TypeError):
        pass  # Expected

    return {
        "probe": "test_immutability_and_slot_protection",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def test_whitespace_and_case_sensitivity_tampering() -> dict[str, Any]:
    """Verify that whitespace padding, case differences, and empty parameters fail closed."""
    violations: list[str] = []

    # 1. Whitespace padding during creation must fail
    whitespace_cases = ["  user_alice", "user_alice  ", "\tuser_alice", "   "]
    for ws in whitespace_cases:
        if ws.strip():  # non-empty after strip, but whitespace padded
            # Creating binding with padded string is valid, BUT matches against clean string must return False
            b = ApprovalBinding(
                action_id="act_01",
                amount=Money(1000, "INR"),
                merchant_id="merch_01",
                resource_id="prod_01",
                currency="INR",
                action_version=1,
                tenant_id="tenant_01",
                principal_id=ws,
            )
            if b.matches(
                action_id="act_01",
                amount=Money(1000, "INR"),
                merchant_id="merch_01",
                resource_id="prod_01",
                currency="INR",
                action_version=1,
                tenant_id="tenant_01",
                principal_id="user_alice",
            ):
                violations.append(f"Whitespace padded principal '{ws}' matched clean 'user_alice'")
        else:
            try:
                ApprovalBinding(
                    action_id="act_01",
                    amount=Money(1000, "INR"),
                    merchant_id="merch_01",
                    resource_id="prod_01",
                    currency="INR",
                    action_version=1,
                    tenant_id="tenant_01",
                    principal_id=ws,
                )
                violations.append(f"Blank principal '{ws}' did not raise InvalidApprovalError")
            except InvalidApprovalError:
                pass  # Expected

    # 2. Case difference must NOT match
    binding_clean = ApprovalBinding(
        action_id="act_01",
        amount=Money(1000, "INR"),
        merchant_id="merch_01",
        resource_id="prod_01",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
        principal_id="user_alice",
    )
    if binding_clean.matches(
        action_id="act_01",
        amount=Money(1000, "INR"),
        merchant_id="merch_01",
        resource_id="prod_01",
        currency="INR",
        action_version=1,
        tenant_id="tenant_01",
        principal_id="USER_ALICE",
    ):
        violations.append("Case-mismatched principal 'USER_ALICE' matched 'user_alice'")

    return {
        "probe": "test_whitespace_and_case_sensitivity_tampering",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def test_cross_tenant_identical_namespaces() -> dict[str, Any]:
    """Verify that two tenants sharing identical principal_id and action_id cannot cross-execute."""
    violations: list[str] = []
    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_alpha")

    # Tenant Alpha: user_alice creates act_01
    action_alpha = _make_action(
        action_id="act_shared_id",
        principal_id="user_alice",
        agent_id="agent_alice",
        tenant_id="tenant_alpha",
    )
    approval_alpha = orch._approval_service.create_approval(action_alpha, tenant_id="tenant_alpha")
    orch._approval_service.grant_approval(
        approval_alpha.approval_id, "mgr_alpha", tenant_id="tenant_alpha"
    )

    # Tenant Beta: identical principal_id 'user_alice' and action_id 'act_shared_id'
    action_beta = _make_action(
        action_id="act_shared_id",
        principal_id="user_alice",
        agent_id="agent_alice",
        tenant_id="tenant_beta",
    )

    # Tenant Beta attempts to execute Tenant Alpha's approval
    try:
        orch.execute_approved_purchase(
            approval_id=approval_alpha.approval_id,
            action=action_beta,
            tenant_id="tenant_beta",
            variant_id="var_01",
            quantity=1,
        )
        violations.append("Cross-tenant execution with identical user/action did not fail closed")
    except (InvalidCommerceError, CrossTenantAccessError, ApprovalBindingMismatchError):
        pass  # Expected fail-closed behavior
    except Exception as exc:
        violations.append(f"Unexpected exception during cross-tenant execution: {exc}")

    # Confirm approval in Tenant Alpha was NOT consumed
    appr_check = orch._approval_repo.get("tenant_alpha", approval_alpha.approval_id)
    if appr_check is None or appr_check.status != ApprovalStatus.APPROVED:
        violations.append(
            f"Tenant Alpha approval corrupted by Tenant Beta attempt: status={getattr(appr_check, 'status', None)}"
        )

    return {
        "probe": "test_cross_tenant_identical_namespaces",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def test_double_spend_http_replay() -> dict[str, Any]:
    """Verify HTTP API returns 200 on first execute and 409 on second execute."""
    violations: list[str] = []
    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    action = _make_action(
        action_id="act_http_replay",
        principal_id="user_01",
        agent_id="agent_01",
        tenant_id="tenant_01",
        resource_id="prod_01",
        amount_minor=129900,
    )
    appr = orch._approval_service.create_approval(action, tenant_id="tenant_01")
    orch._approval_service.grant_approval(appr.approval_id, "mgr", tenant_id="tenant_01")

    # Execution 1: Must succeed (HTTP 200)
    status1, body1 = client.post(
        f"/api/v1/approvals/{appr.approval_id}/execute",
        json_body={
            "action_id": "act_http_replay",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 129900,
            "currency": "INR",
        },
        headers={"x-tenant-id": "tenant_01"},
    )
    if status1 != 200:
        violations.append(f"First execution failed: HTTP {status1}: {body1}")

    # Execution 2: Must fail with HTTP 409 APPROVAL_ALREADY_CONSUMED
    status2, body2 = client.post(
        f"/api/v1/approvals/{appr.approval_id}/execute",
        json_body={
            "action_id": "act_http_replay",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 129900,
            "currency": "INR",
        },
        headers={"x-tenant-id": "tenant_01"},
    )
    if status2 != 409:
        violations.append(f"Second execution expected HTTP 409, got {status2}: {body2}")
    err_code = body2.get("error", {}).get("code")
    if err_code != "APPROVAL_ALREADY_CONSUMED":
        violations.append(f"Unexpected error code on replay: {err_code}")

    return {
        "probe": "test_double_spend_http_replay",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def test_currency_and_type_invariants() -> dict[str, Any]:
    """Verify that currency mismatch and invalid types strictly fail closed."""
    violations: list[str] = []

    # 1. Currency mismatch between amount and binding
    try:
        ApprovalBinding(
            action_id="act_01",
            amount=Money(1000, "USD"),
            merchant_id="merch_01",
            resource_id="prod_01",
            currency="INR",  # Mismatch!
            action_version=1,
            tenant_id="tenant_01",
            principal_id="user_alice",
        )
        violations.append("Currency mismatch did not raise InvalidApprovalError")
    except InvalidApprovalError:
        pass

    # 2. Boolean action_version
    try:
        ApprovalBinding(
            action_id="act_01",
            amount=Money(1000, "INR"),
            merchant_id="merch_01",
            resource_id="prod_01",
            currency="INR",
            action_version=True,  # type: ignore[arg-type]
            tenant_id="tenant_01",
            principal_id="user_alice",
        )
        violations.append("Boolean action_version did not raise InvalidApprovalError")
    except InvalidApprovalError:
        pass

    # 3. Non-Money amount
    try:
        ApprovalBinding(
            action_id="act_01",
            amount=1000,  # type: ignore[arg-type]
            merchant_id="merch_01",
            resource_id="prod_01",
            currency="INR",
            action_version=1,
            tenant_id="tenant_01",
            principal_id="user_alice",
        )
        violations.append("Integer amount did not raise InvalidApprovalError")
    except InvalidApprovalError:
        pass

    return {
        "probe": "test_currency_and_type_invariants",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def test_30_thread_concurrent_sqla_purchase_race() -> dict[str, Any]:
    """Verify that 30 threads racing execute_approved_purchase on SQLAlchemy results in exactly 1 order."""
    violations: list[str] = []

    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator(tenant_id="tenant_sqla_30")

    # Wire a persistent in-memory SQLite repository with StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_sqla_30", name="SQLA Org"))
    sqla_approval_repo = SqlAlchemyApprovalRepository(session_factory)

    # Replace approval repo and service in orchestrator
    orch._approval_repo = sqla_approval_repo
    orch._approval_service = ApprovalService(sqla_approval_repo)

    action = _make_action(
        action_id="act_race_30",
        tenant_id="tenant_sqla_30",
        resource_id="prod_01",
        amount_minor=129900,
        agent_id="agent_01",
        principal_id="user_01",
    )
    appr = orch._approval_service.create_approval(action, tenant_id="tenant_sqla_30")
    orch._approval_service.grant_approval(appr.approval_id, "mgr", tenant_id="tenant_sqla_30")

    results: list[ActionStatus | str] = []
    lock = threading.Lock()

    def worker_execute() -> None:
        try:
            res = orch.execute_approved_purchase(
                approval_id=appr.approval_id,
                action=action,
                tenant_id="tenant_sqla_30",
                variant_id="var_01",
                quantity=1,
            )
            with lock:
                results.append(res.status)
        except ApprovalAlreadyConsumedError:
            with lock:
                results.append("CONSUMED_REJECTED")
        except Exception as exc:
            with lock:
                results.append(f"ERROR: {type(exc).__name__}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(worker_execute) for _ in range(30)]
        concurrent.futures.wait(futures)

    completed_count = results.count(ActionStatus.COMPLETED)
    rejected_count = results.count("CONSUMED_REJECTED")

    if completed_count != 1:
        violations.append(
            f"Expected exactly 1 successful purchase out of 30 threads, got {completed_count}. Results: {results}"
        )
    if rejected_count != 29:
        violations.append(
            f"Expected exactly 29 CONSUMED_REJECTED rejections, got {rejected_count}. Results: {results}"
        )

    # Verify database final state
    final_appr = sqla_approval_repo.get("tenant_sqla_30", appr.approval_id)
    if final_appr is None or final_appr.status != ApprovalStatus.CONSUMED:
        violations.append(
            f"Final approval status not CONSUMED: {getattr(final_appr, 'status', None)}"
        )

    engine.dispose()

    return {
        "probe": "test_30_thread_concurrent_sqla_purchase_race",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def main() -> int:
    log("=" * 72)
    log("AGENTPAY CHALLENGER: MILESTONE 4 ADVANCED ADVERSARIAL EDGE HARNESS")
    log("=" * 72)

    probes = [
        ("test_immutability_and_slot_protection", test_immutability_and_slot_protection),
        (
            "test_whitespace_and_case_sensitivity_tampering",
            test_whitespace_and_case_sensitivity_tampering,
        ),
        ("test_cross_tenant_identical_namespaces", test_cross_tenant_identical_namespaces),
        ("test_double_spend_http_replay", test_double_spend_http_replay),
        ("test_currency_and_type_invariants", test_currency_and_type_invariants),
        (
            "test_30_thread_concurrent_sqla_purchase_race",
            test_30_thread_concurrent_sqla_purchase_race,
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
        log("[+] VERDICT: ALL CHALLENGER EDGE-CASE PROBES SATISFIED -> PASS")
        return 0
    else:
        log("[-] VERDICT: VULNERABILITIES DETECTED -> FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Empirical Adversarial & Cryptographic Tamper Verification Suite (§177).

Challenger 2 verification suite for Milestone 1:
- Challenge Vector 14: Audit event metadata tampering across all positions (0, middle, last)
  and all metadata fields (actor, event_type, timestamp, tenant_id, agent_id, action_id).
- Challenge Vector 8 & 12: Subtle evidence price discrepancies (1 minor unit under/over,
  currency mismatch, stale price drift, inventory depletion preflight).
- Challenge Vector 9: Merchant status tampering (single-step and multi-step flows,
  merchant transitioning from ACTIVE to SUSPENDED, ghost/missing merchant fail-open vulnerability).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.application.services.approval_service import ApprovalService
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.evidence import (
    ConflictSeverity,
    ConflictType,
    EvidenceConflictDetector,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import PolicyDecision

# ===========================================================================
# Vector 14: Cryptographic Integrity & Metadata Tampering (§29, §45, §177)
# ===========================================================================


def _build_populated_audit_ledger(
    tenant_id: str = "tenant_crypto_challenger",
) -> InMemoryAuditLedger:
    ledger = InMemoryAuditLedger()
    event_specs: list[tuple[AuditEventType, str, str | None, dict[str, Any]]] = [
        (
            AuditEventType.ACTION_REQUESTED,
            "agent_01",
            "act_01",
            {"action": "purchase", "amount": 149900},
        ),
        (
            AuditEventType.PRODUCT_SEARCHED,
            "agent_01",
            "act_01",
            {"query": "keyboard", "results": 3},
        ),
        (AuditEventType.EVIDENCE_RETRIEVED, "agent_01", "act_01", {"price": 149900, "stock": 10}),
        (AuditEventType.POLICY_EVALUATED, "policy_engine", "act_01", {"decision": "REVIEW"}),
        (AuditEventType.APPROVAL_REQUESTED, "system", "act_01", {"approval_id": "appr_01"}),
        (AuditEventType.APPROVAL_GRANTED, "human_approver", "act_01", {"approver": "jane"}),
        (AuditEventType.PAYMENT_CREATED, "payment_provider", "act_01", {"status": "SUCCESS"}),
        (AuditEventType.ORDER_CREATED, "order_service", "act_01", {"order_id": "ord_01"}),
    ]

    for ev_type, actor, act_id, data in event_specs:
        ledger.record_event(
            event_type=ev_type,
            action_id=act_id,
            tenant_id=tenant_id,
            actor=actor,
            data=data,
            agent_id="agent_01" if actor.startswith("agent") else None,
        )
    return ledger


def test_vector_14_audit_chain_baseline_validity() -> None:
    """Vector 14: Unmodified audit event stream verifies successfully."""
    tenant = "tenant_valid_chain"
    ledger = _build_populated_audit_ledger(tenant)
    assert ledger.verify_chain(tenant) is True


@pytest.mark.parametrize(
    "position_label,pos_index",
    [
        ("genesis_position_0", 0),
        ("early_position_1", 1),
        ("middle_position_3", 3),
        ("penultimate_position_6", 6),
        ("last_position_7", 7),
    ],
)
@pytest.mark.parametrize(
    "field_name",
    [
        "actor",
        "event_type",
        "timestamp",
        "tenant_id",
        "agent_id",
        "action_id",
        "data",
        "previous_event_hash",
    ],
)
def test_vector_14_metadata_tampering_at_all_positions(
    position_label: str,
    pos_index: int,
    field_name: str,
) -> None:
    """Vector 14: Assert verify_chain() detects 100% of metadata tampering across all positions."""
    tenant = f"tenant_{position_label}_{field_name}"
    ledger = _build_populated_audit_ledger(tenant)
    events = ledger._tenant_events[tenant]
    target_event = events[pos_index]

    orig_value = getattr(target_event, field_name)
    tampered_value: Any

    if field_name == "actor":
        tampered_value = "forged_malicious_actor"
    elif field_name == "event_type":
        tampered_value = (
            AuditEventType.EMERGENCY_PAUSE
            if target_event.event_type != AuditEventType.EMERGENCY_PAUSE
            else AuditEventType.ORDER_CANCELLED
        )
    elif field_name == "timestamp":
        tampered_value = orig_value + timedelta(seconds=1)
    elif field_name == "tenant_id":
        tampered_value = "forged_tenant_infiltrator"
    elif field_name == "agent_id":
        tampered_value = "forged_agent_999" if orig_value is None else None
    elif field_name == "action_id":
        tampered_value = "forged_act_hijack" if orig_value is None else None
    elif field_name == "data":
        tampered_value = {**orig_value, "tampered_key": "tampered_value"}
    elif field_name == "previous_event_hash":
        tampered_value = "f" * 64
    else:
        pytest.fail(f"Unknown field_name {field_name}")

    # Inject tamper
    object.__setattr__(target_event, field_name, tampered_value)
    # MUST FAIL CLOSED: verify_chain() must return False
    assert ledger.verify_chain(tenant) is False, (
        f"Security Failure: verify_chain() failed to detect tampering on {field_name} at position {pos_index} ({position_label})"
    )

    # Restore and verify chain validity returns
    object.__setattr__(target_event, field_name, orig_value)
    assert ledger.verify_chain(tenant) is True


def test_vector_14_structural_stream_tampering_deletion_and_reorder() -> None:
    """Vector 14: Structural stream modifications (event deletion, swapping) fail verification."""
    tenant = "tenant_structural_tamper"
    ledger = _build_populated_audit_ledger(tenant)
    events = ledger._tenant_events[tenant]

    # 1. Swapping events 2 and 3 breaks the chain
    events[2], events[3] = events[3], events[2]
    assert ledger.verify_chain(tenant) is False
    events[2], events[3] = events[3], events[2]
    assert ledger.verify_chain(tenant) is True

    # 2. Deleting an intermediate event breaks previous_event_hash linkage
    removed = events.pop(2)
    assert ledger.verify_chain(tenant) is False
    events.insert(2, removed)
    assert ledger.verify_chain(tenant) is True

    # 3. Deleting genesis event breaks expected GENESIS_HASH linkage
    genesis = events.pop(0)
    assert ledger.verify_chain(tenant) is False
    events.insert(0, genesis)
    assert ledger.verify_chain(tenant) is True


@given(
    target_pos=st.integers(min_value=0, max_value=7),
    field_choice=st.sampled_from(
        ["actor", "event_type", "timestamp", "tenant_id", "agent_id", "action_id"]
    ),
)
@settings(max_examples=50)
def test_vector_14_property_based_tamper_detection(target_pos: int, field_choice: str) -> None:
    """Vector 14: Property-based verification that any metadata mutation is detected without exception."""
    tenant = f"tenant_prop_{target_pos}_{field_choice}"
    ledger = _build_populated_audit_ledger(tenant)
    events = ledger._tenant_events[tenant]
    ev = events[target_pos]

    orig = getattr(ev, field_choice)
    mutation: Any
    if field_choice == "actor":
        mutation = "prop_tampered_actor"
    elif field_choice == "event_type":
        mutation = (
            AuditEventType.EMERGENCY_PAUSE
            if ev.event_type != AuditEventType.EMERGENCY_PAUSE
            else AuditEventType.ACTION_REQUESTED
        )
    elif field_choice == "timestamp":
        mutation = orig + timedelta(microseconds=500)
    elif field_choice == "tenant_id":
        mutation = "prop_tampered_tenant"
    elif field_choice == "agent_id":
        mutation = "prop_agent" if orig is None else None
    elif field_choice == "action_id":
        mutation = "prop_action" if orig is None else None

    object.__setattr__(ev, field_choice, mutation)
    assert ledger.verify_chain(tenant) is False
    object.__setattr__(ev, field_choice, orig)
    assert ledger.verify_chain(tenant) is True


# ===========================================================================
# Vector 8 & 12: Subtle Evidence Price Discrepancies (§32, §33, §177)
# ===========================================================================


def test_vector_08_subtle_price_discrepancy_one_minor_unit_under() -> None:
    """Vector 8: Asserted price underpaid by exactly 1 minor unit (paise) fails closed before payment."""
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator()

    # Base variant is 149900 INR (₹1,499.00)
    action_under = ActionRequest(
        action_id="act_v08_under_1",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="subtle underpayment probe"),
        amount=Money(minor_units=149899, currency="INR"),  # 1 minor unit under
    )

    res = orch.execute_purchase(action=action_under, tenant_id="tenant_01", variant_id="var_01")
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(res.conflicts) >= 1
    assert res.conflicts[0].conflict_type == ConflictType.PRICE_MISMATCH
    assert res.conflicts[0].severity == ConflictSeverity.CRITICAL
    assert len(payment_provider._attempts) == 0


def test_vector_08_subtle_price_discrepancy_one_minor_unit_over() -> None:
    """Vector 8: Asserted price overpaid by exactly 1 minor unit (paise) fails closed before payment."""
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator()

    action_over = ActionRequest(
        action_id="act_v08_over_1",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="subtle overpayment probe"),
        amount=Money(minor_units=149901, currency="INR"),  # 1 minor unit over
    )

    res = orch.execute_purchase(action=action_over, tenant_id="tenant_01", variant_id="var_01")
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(res.conflicts) >= 1
    assert res.conflicts[0].conflict_type == ConflictType.PRICE_MISMATCH
    assert len(payment_provider._attempts) == 0


def test_vector_08_subtle_price_discrepancy_multi_quantity() -> None:
    """Vector 8: Multi-quantity price calculation discrepancy fails closed."""
    orch, _, _, _, _, _, variant_repo, inv_repo, payment_provider, _, _ = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_multi",
            product_id="prod_01",
            sku="SKU-MULTI",
            name="Multi Item",
            price=Money(50000, "INR"),  # ₹500.00
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_multi", tenant_id="tenant_01", available=10, reserved=0),
    )

    # 3 units @ ₹500.00 = ₹1,500.00 (150000 minor units)
    # Agent asserts 149999 (off by 1 minor unit)
    action_multi = ActionRequest(
        action_id="act_v08_multi_qty",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="multi quantity math error probe"),
        amount=Money(minor_units=149999, currency="INR"),
    )

    res = orch.execute_purchase(
        action=action_multi, tenant_id="tenant_01", variant_id="var_multi", quantity=3
    )
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(res.conflicts) >= 1
    assert res.conflicts[0].conflict_type == ConflictType.PRICE_MISMATCH
    assert len(payment_provider._attempts) == 0


def test_vector_08_subtle_currency_mismatch_identical_numerics() -> None:
    """Vector 8: Identical minor unit amounts with differing ISO currencies fail closed."""
    orch, _, _, _, _, _, _, _, payment_provider, _, _ = setup_orchestrator()

    currencies_to_probe = ["USD", "EUR", "GBP", "JPY", "AUD"]
    for curr in currencies_to_probe:
        action_curr = ActionRequest(
            action_id=f"act_v08_curr_{curr}",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product", id="prod_01"),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="currency confusion probe"),
            amount=Money(minor_units=149900, currency=curr),
        )
        res = orch.execute_purchase(action=action_curr, tenant_id="tenant_01", variant_id="var_01")
        assert res.status == ActionStatus.FAILED
        assert res.decision == PolicyDecision.DENY
        assert len(res.conflicts) >= 1
        assert res.conflicts[0].conflict_type == ConflictType.PRICE_MISMATCH
        assert len(payment_provider._attempts) == 0


def test_vector_12_stale_data_preflight_price_drift_one_minor_unit() -> None:
    """Vector 12: In-flight merchant price drift of 1 minor unit caught preflight."""
    orch, _, _, _, _, _, variant_repo, _, payment_provider, _, _ = setup_orchestrator()

    # Agent quotes ₹1,499.00
    action = ActionRequest(
        action_id="act_v12_drift",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="preflight drift probe"),
        amount=Money(minor_units=149900, currency="INR"),
    )

    # Merchant catalog subtly drifts by 1 paise (149901) right before checkout
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_01",
            product_id="prod_01",
            sku="KB-01",
            name="Keyboard",
            price=Money(minor_units=149901, currency="INR"),
        ),
    )

    res = orch.execute_purchase(action=action, tenant_id="tenant_01", variant_id="var_01")
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(payment_provider._attempts) == 0


def test_evidence_conflict_detector_direct_matrix() -> None:
    """Vector 8 & 12: Direct boundary matrix evaluation on EvidenceConflictDetector."""
    m_100_inr = Money(100, "INR")
    m_99_inr = Money(99, "INR")
    m_101_inr = Money(101, "INR")
    m_100_usd = Money(100, "USD")

    # Under by 1
    c1 = EvidenceConflictDetector.detect_price_conflict(m_99_inr, m_100_inr, "tenant_01", "res_01")
    assert c1 is not None
    assert c1.conflict_type == ConflictType.PRICE_MISMATCH
    assert c1.severity == ConflictSeverity.CRITICAL

    # Over by 1
    c2 = EvidenceConflictDetector.detect_price_conflict(m_101_inr, m_100_inr, "tenant_01", "res_01")
    assert c2 is not None
    assert c2.conflict_type == ConflictType.PRICE_MISMATCH

    # Currency mismatch
    c3 = EvidenceConflictDetector.detect_price_conflict(m_100_usd, m_100_inr, "tenant_01", "res_01")
    assert c3 is not None
    assert c3.conflict_type == ConflictType.PRICE_MISMATCH

    # Exact match
    c4 = EvidenceConflictDetector.detect_price_conflict(m_100_inr, m_100_inr, "tenant_01", "res_01")
    assert c4 is None


# ===========================================================================
# Vector 9: Merchant Status Tampering (§30, §37, §177)
# ===========================================================================


def test_vector_09_single_step_merchant_suspension_fails_closed() -> None:
    """Vector 9: Merchant in SUSPENDED status fails closed during single-step autonomous purchase."""
    orch, _, _, _, merchant_repo, product_repo, variant_repo, inv_repo, payment_provider, _, _ = (
        setup_orchestrator()
    )

    merchant_repo.save(
        "tenant_01",
        Merchant(
            merchant_id="merch_susp_single",
            tenant_id="tenant_01",
            name="Suspended Corp",
            status=MerchantStatus.SUSPENDED,
            allowed_agent_purchase=True,
        ),
    )
    product_repo.save(
        "tenant_01",
        Product(
            product_id="prod_susp_single",
            tenant_id="tenant_01",
            merchant_id="merch_susp_single",
            name="Susp Item",
            category="electronics",
            description="desc",
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_susp_single",
            product_id="prod_susp_single",
            sku="SKU-SS",
            name="SS",
            price=Money(100000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_susp_single", tenant_id="tenant_01", available=5, reserved=0),
    )

    action = ActionRequest(
        action_id="act_v09_susp_single",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_susp_single"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="susp buy"),
        amount=Money(100000, "INR"),
    )

    res = orch.execute_purchase(action, "tenant_01", "var_susp_single")
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert "suspended" in (res.rejection_reason or "").lower()
    assert len(payment_provider._attempts) == 0


def test_vector_09_empirical_proof_multistep_suspension_fails_open() -> None:
    """Vector 9 Defect Reproduction: Demonstrates that execute_approved_purchase currently fails open

    when a merchant transitions from ACTIVE to SUSPENDED between approval and execution (§2.4, §30).
    """
    (
        orch,
        _,
        _,
        _,
        merchant_repo,
        _,
        variant_repo,
        inv_repo,
        payment_provider,
        _,
        approval_repo,
    ) = setup_orchestrator()

    # 1. Setup high-value item (₹8,000) requiring approval under active merchant
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_appr_probe",
            product_id="prod_01",
            sku="SKU-PROBE",
            name="Probe Item",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_appr_probe", tenant_id="tenant_01", available=5, reserved=0),
    )

    action = ActionRequest(
        action_id="act_v09_multistep_probe",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="multistep status probe"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    # Step 1: Request purchase -> Evaluates to REVIEW, creates approval
    r1 = orch.execute_purchase(action, "tenant_01", "var_appr_probe")
    assert r1.status == ActionStatus.AWAITING_APPROVAL
    assert r1.decision == PolicyDecision.REVIEW
    assert r1.approval is not None
    approval_id = r1.approval.approval_id

    # Step 2: Human grants approval
    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(approval_id, "human_approver", "tenant_01")

    # Step 3: TAMPER / TRANSITION: Merchant is SUSPENDED before execution
    merchant = merchant_repo.get("tenant_01", "merch_01")
    assert merchant is not None
    suspended_merchant = Merchant(
        merchant_id="merch_01",
        tenant_id="tenant_01",
        name=merchant.name,
        status=MerchantStatus.SUSPENDED,
        allowed_agent_purchase=True,
    )
    merchant_repo.save("tenant_01", suspended_merchant)

    # Step 4: Execute approved purchase
    r2 = orch.execute_approved_purchase(approval_id, action, "tenant_01", "var_appr_probe")

    # VERIFIED FAIL CLOSED: Suspended merchant execution is denied and no payment is captured (§2.4, §30)
    assert r2.status == ActionStatus.FAILED
    assert r2.decision == PolicyDecision.DENY
    assert len(payment_provider._attempts) == 0


def test_vector_09_multistep_suspension_contract_must_fail_closed() -> None:
    """Vector 9 Security Specification: execute_approved_purchase MUST fail closed when merchant is suspended."""
    (
        orch,
        _,
        _,
        _,
        merchant_repo,
        _,
        variant_repo,
        inv_repo,
        payment_provider,
        _,
        approval_repo,
    ) = setup_orchestrator()

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_appr_contract",
            product_id="prod_01",
            sku="SKU-CONTRACT",
            name="Contract Item",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_appr_contract", tenant_id="tenant_01", available=5, reserved=0),
    )

    action = ActionRequest(
        action_id="act_v09_contract",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="multistep contract"),
        amount=Money(800000, "INR"),
        authorization_mode=AuthorizationMode.SUPERVISED,
    )

    r1 = orch.execute_purchase(action, "tenant_01", "var_appr_contract")
    assert r1.approval is not None
    approval_svc = ApprovalService(approval_repo=approval_repo)
    approval_svc.grant_approval(r1.approval.approval_id, "human_approver", "tenant_01")

    # Suspend merchant
    merchant = merchant_repo.get("tenant_01", "merch_01")
    assert merchant is not None
    merchant_repo.save(
        "tenant_01",
        Merchant(
            merchant_id="merch_01",
            tenant_id="tenant_01",
            name=merchant.name,
            status=MerchantStatus.SUSPENDED,
            allowed_agent_purchase=True,
        ),
    )

    # REQUIRED BEHAVIOR: Must fail closed or raise an error!
    res = orch.execute_approved_purchase(
        r1.approval.approval_id, action, "tenant_01", "var_appr_contract"
    )
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(payment_provider._attempts) == 0


def test_vector_09_empirical_proof_ghost_merchant_fails_open() -> None:
    """Vector 9: Verify execute_purchase fails closed when merchant is not in merchant_repo (§2.4)."""
    orch, _, _, _, _, product_repo, variant_repo, inv_repo, payment_provider, _, _ = (
        setup_orchestrator()
    )

    product_repo.save(
        "tenant_01",
        Product(
            product_id="prod_ghost",
            tenant_id="tenant_01",
            merchant_id="merch_nonexistent_ghost",
            name="Ghost Product",
            category="electronics",
            description="desc",
        ),
    )
    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_ghost",
            product_id="prod_ghost",
            sku="SKU-GHOST",
            name="Ghost Item",
            price=Money(100000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(variant_id="var_ghost", tenant_id="tenant_01", available=5, reserved=0),
    )

    action = ActionRequest(
        action_id="act_ghost_probe",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_ghost"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="ghost merchant probe"),
        amount=Money(100000, "INR"),
    )

    res = orch.execute_purchase(action, "tenant_01", "var_ghost")

    # VERIFIED FAIL CLOSED: Ghost merchant purchase fails closed (§2.4)
    assert res.status == ActionStatus.FAILED
    assert res.decision == PolicyDecision.DENY
    assert len(payment_provider._attempts) == 0

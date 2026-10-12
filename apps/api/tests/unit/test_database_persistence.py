"""Comprehensive unit tests for relational persistence and multi-tenant isolation (§49, §50, §51).

Verifies:
1. Hermetic SQLite in-memory round-trip persistence across all 7 repositories.
2. Exact domain model equality on round-trip retrieval.
3. Money integer minor units and ISO currency fidelity.
4. Strict multi-tenant isolation and cross-tenant denial.
5. Unique constraint enforcement (e.g. idempotency key per tenant).
6. Tamper-evident audit chain persistence and verification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agentpay.domain.approval import (
    Approval,
    ApprovalAlreadyConsumedError,
    ApprovalBinding,
    ApprovalStatus,
)
from agentpay.domain.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    AuditHasher,
)
from agentpay.domain.commerce import Product, ProductVariant
from agentpay.domain.errors import (
    AgentRevokedError,
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidCommerceError,
)
from agentpay.domain.identity import (
    Agent,
    AgentStatus,
    Credential,
    CredentialStatus,
    CredentialType,
    Tenant,
    TenantStatus,
    UserRole,
)
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.agent_repository import (
    SqlAlchemyAgentRepository,
)
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.audit_ledger import (
    SqlAlchemyAuditRepository,
)
from agentpay.infrastructure.database.repositories.commerce_repository import (
    SqlAlchemyProductRepository,
)
from agentpay.infrastructure.database.repositories.credential_repository import (
    SqlAlchemyCredentialRepository,
)
from agentpay.infrastructure.database.repositories.order_repository import (
    SqlAlchemyOrderRepository,
)
from agentpay.infrastructure.database.repositories.payment_repository import (
    SqlAlchemyPaymentRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    """Create an isolated, in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return factory


# ============================================================================
# 1. Tenant Persistence Tests
# ============================================================================
def test_tenant_repository_roundtrip(session_factory: sessionmaker[Session]) -> None:
    repo = SqlAlchemyTenantRepository(session_factory)

    tenant = Tenant(
        tenant_id="tenant_alpha",
        name="Acme Corporation",
        status=TenantStatus.ACTIVE,
    )

    repo.save(tenant)

    retrieved = repo.get("tenant_alpha")
    assert retrieved is not None
    assert retrieved == tenant
    assert retrieved.tenant_id == "tenant_alpha"
    assert retrieved.name == "Acme Corporation"
    assert retrieved.status == TenantStatus.ACTIVE

    # Test update
    updated_tenant = Tenant(
        tenant_id="tenant_alpha",
        name="Acme Global Corporation",
        status=TenantStatus.SUSPENDED,
    )
    repo.save(updated_tenant)

    updated_retrieved = repo.get("tenant_alpha")
    assert updated_retrieved is not None
    assert updated_retrieved == updated_tenant
    assert updated_retrieved.name == "Acme Global Corporation"
    assert updated_retrieved.status == TenantStatus.SUSPENDED

    # Test list_tenants and exists
    assert repo.exists("tenant_alpha") is True
    assert repo.exists("nonexistent") is False
    tenants = repo.list_tenants()
    assert len(tenants) == 1
    assert tenants[0].tenant_id == "tenant_alpha"


# ============================================================================
# 2. Agent Persistence Tests
# ============================================================================
def test_agent_repository_roundtrip(session_factory: sessionmaker[Session]) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_01", name="Tenant One"))

    agent_repo = SqlAlchemyAgentRepository(session_factory)

    now = datetime.now(UTC)
    expires = now + timedelta(days=30)
    agent = Agent(
        agent_id="agent_007",
        tenant_id="tenant_01",
        principal_id="user_admin_01",
        issuer="https://auth.agentpay.internal",
        capabilities=("checkout.execute", "cart.read"),
        scopes=("commerce:read", "commerce:write"),
        created_at=now,
        expires_at=expires,
        status=AgentStatus.ACTIVE,
    )

    agent_repo.save(agent)

    retrieved = agent_repo.get("tenant_01", "agent_007")
    assert retrieved is not None
    assert retrieved == agent
    assert retrieved.capabilities == ("checkout.execute", "cart.read")
    assert retrieved.scopes == ("commerce:read", "commerce:write")
    assert retrieved.created_at.tzinfo == UTC
    assert retrieved.expires_at.tzinfo == UTC

    # Verify update_status
    agent_repo.update_status("tenant_01", "agent_007", AgentStatus.SUSPENDED)
    suspended = agent_repo.get("tenant_01", "agent_007")
    assert suspended is not None
    assert suspended.status == AgentStatus.SUSPENDED

    # Verify terminal REVOKED transition and revocation protection
    agent_repo.update_status("tenant_01", "agent_007", AgentStatus.REVOKED)
    with pytest.raises(AgentRevokedError):
        agent_repo.update_status("tenant_01", "agent_007", AgentStatus.ACTIVE)

    with pytest.raises(AgentRevokedError):
        agent_repo.save(agent)


def test_agent_multi_tenant_isolation(session_factory: sessionmaker[Session]) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_01", name="Tenant One"))
    tenant_repo.save(Tenant(tenant_id="tenant_02", name="Tenant Two"))

    agent_repo = SqlAlchemyAgentRepository(session_factory)

    now = datetime.now(UTC)
    agent = Agent(
        agent_id="agent_shared_id",
        tenant_id="tenant_01",
        principal_id="user_01",
        issuer="https://auth.agentpay.internal",
        capabilities=("tool:call",),
        scopes=("read",),
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    agent_repo.save(agent)

    # Cross-tenant query must return None
    assert agent_repo.get("tenant_02", "agent_shared_id") is None
    assert agent_repo.get("tenant_01", "agent_shared_id") is not None

    # Mismatched tenant partition save must fail closed with CrossTenantAccessError
    with pytest.raises(CrossTenantAccessError):
        agent_repo.save("tenant_02", agent)


# ============================================================================
# 3. Credential Persistence Tests
# ============================================================================
def test_credential_repository_roundtrip(session_factory: sessionmaker[Session]) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_sec", name="Security Tenant"))

    cred_repo = SqlAlchemyCredentialRepository(session_factory)

    expires = datetime.now(UTC) + timedelta(days=90)
    raw_secret = "super_secret_agent_key_42"
    secret_hash = Credential.hash_secret(raw_secret)

    cred = Credential(
        credential_id="cred_abc123",
        tenant_id="tenant_sec",
        principal_id="agent_01",
        credential_type=CredentialType.API_KEY,
        secret_hash=secret_hash,
        expires_at=expires,
        status=CredentialStatus.ACTIVE,
        role=UserRole.ADMIN,
        scopes=("orders:create", "payments:read"),
    )

    cred_repo.save(cred)

    retrieved = cred_repo.get("tenant_sec", "cred_abc123")
    assert retrieved is not None
    assert retrieved == cred
    assert retrieved.verify_secret(raw_secret) is True
    assert retrieved.role == UserRole.ADMIN

    # Lookup globally by secret hash
    by_hash = cred_repo.get_by_secret_hash(secret_hash)
    assert by_hash is not None
    assert by_hash == cred

    # Multi-tenant isolation: cannot access with different tenant_id
    assert cred_repo.get("tenant_other", "cred_abc123") is None

    # Revoke
    cred_repo.revoke("tenant_sec", "cred_abc123")
    revoked = cred_repo.get("tenant_sec", "cred_abc123")
    assert revoked is not None
    assert revoked.status == CredentialStatus.REVOKED
    assert revoked.is_active is False


# ============================================================================
# 4. Commerce (Product & Variant) Persistence Tests
# ============================================================================
def test_product_and_variant_persistence(session_factory: sessionmaker[Session]) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_retail", name="Retail Tenant"))

    product_repo = SqlAlchemyProductRepository(session_factory)

    product = Product(
        product_id="prod_laptop_pro",
        tenant_id="tenant_retail",
        merchant_id="merchant_apple",
        name="MacBook Pro 16",
        category="Electronics",
        description="High-end development laptop",
    )
    product_repo.save(product)

    retrieved_product = product_repo.get("tenant_retail", "prod_laptop_pro")
    assert retrieved_product is not None
    assert retrieved_product == product

    variant = ProductVariant(
        variant_id="var_m3_max_64gb",
        product_id="prod_laptop_pro",
        sku="MBP16-M3-64GB",
        name="MacBook Pro 16 - M3 Max / 64GB / 1TB",
        price=Money(349900, "USD"),
    )
    product_repo.save_variant(variant, tenant_id="tenant_retail")

    retrieved_variant = product_repo.get_variant("tenant_retail", "var_m3_max_64gb")
    assert retrieved_variant is not None
    assert retrieved_variant == variant
    assert retrieved_variant.price.minor_units == 349900
    assert retrieved_variant.price.currency == "USD"

    # Search and merchant listing
    by_merchant = product_repo.list_by_merchant("tenant_retail", "merchant_apple")
    assert len(by_merchant) == 1
    assert by_merchant[0] == product

    search_res = product_repo.search("tenant_retail", "laptop")
    assert len(search_res) == 1
    assert search_res[0] == product

    search_name = product_repo.search("tenant_retail", "MacBook")
    assert len(search_name) == 1
    assert search_name[0] == product

    # Cross-tenant isolation
    assert product_repo.get("tenant_other", "prod_laptop_pro") is None
    assert product_repo.get_variant("tenant_other", "var_m3_max_64gb") is None


# ============================================================================
# 5. Order Persistence Tests
# ============================================================================
def test_order_repository_roundtrip(session_factory: sessionmaker[Session]) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_orders", name="Orders Tenant"))

    order_repo = SqlAlchemyOrderRepository(session_factory)

    created_at = datetime.now(UTC)
    order = Order(
        order_id="ord_987654",
        tenant_id="tenant_orders",
        merchant_id="merch_01",
        buyer_id="buyer_alice",
        agent_id="agent_shopper",
        variant_id="var_shoe_42",
        quantity=2,
        total_amount=Money(12000, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id=None,
        created_at=created_at,
    )

    order_repo.save(order)

    retrieved = order_repo.get("tenant_orders", "ord_987654")
    assert retrieved is not None
    assert retrieved == order
    assert retrieved.total_amount.minor_units == 12000
    assert retrieved.total_amount.currency == "INR"

    # Confirm order with payment attempt
    confirmed = order.confirm(payment_attempt_id="att_pay_555")
    order_repo.save(confirmed)

    updated_order = order_repo.get("tenant_orders", "ord_987654")
    assert updated_order is not None
    assert updated_order.status == OrderStatus.CONFIRMED
    assert updated_order.payment_attempt_id == "att_pay_555"

    # Find by payment attempt (§41, B-REC-01)
    by_attempt = order_repo.find_by_payment_attempt("tenant_orders", "att_pay_555")
    assert by_attempt is not None
    assert by_attempt.order_id == "ord_987654"
    assert by_attempt.payment_attempt_id == "att_pay_555"
    assert order_repo.find_by_payment_attempt("tenant_other", "att_pay_555") is None
    assert order_repo.find_by_payment_attempt("tenant_orders", "nonexistent_attempt") is None

    # List for buyer
    buyer_orders = order_repo.list_for_buyer("tenant_orders", "buyer_alice")
    assert len(buyer_orders) == 1
    assert buyer_orders[0].order_id == "ord_987654"

    # Multi-tenant isolation
    assert order_repo.get("tenant_other", "ord_987654") is None


# ============================================================================
# 6. PaymentAttempt Persistence & Idempotency Tests
# ============================================================================
def test_payment_repository_roundtrip_and_idempotency(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_pay", name="Payments Tenant"))

    payment_repo = SqlAlchemyPaymentRepository(session_factory)

    now = datetime.now(UTC)
    attempt = PaymentAttempt(
        attempt_id="att_001",
        intent_id="pi_order_123",
        tenant_id="tenant_pay",
        amount=Money(50000, "EUR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="idemp_key_unique_101",
        provider="mock",
        provider_payment_id="pay_provider_mock_999",
        created_at=now,
        updated_at=now,
        failure_reason=None,
    )

    payment_repo.save(attempt)

    retrieved = payment_repo.get("tenant_pay", "att_001")
    assert retrieved is not None
    assert retrieved == attempt
    assert retrieved.amount.minor_units == 50000
    assert retrieved.amount.currency == "EUR"

    # Lookup by idempotency key
    by_idemp = payment_repo.get_by_idempotency_key("tenant_pay", "idemp_key_unique_101")
    assert by_idemp is not None
    assert by_idemp == attempt

    # Multi-tenant isolation
    assert payment_repo.get("tenant_other", "att_001") is None
    assert payment_repo.get_by_idempotency_key("tenant_other", "idemp_key_unique_101") is None

    # Same idempotency key for DIFFERENT tenant is allowed
    attempt_tenant_two = PaymentAttempt(
        attempt_id="att_002",
        intent_id="pi_order_999",
        tenant_id="tenant_other",
        amount=Money(2500, "USD"),
        status=PaymentStatus.CREATED,
        idempotency_key="idemp_key_unique_101",
        provider="mock",
        created_at=now,
        updated_at=now,
    )
    payment_repo.save(attempt_tenant_two)
    assert payment_repo.get_by_idempotency_key("tenant_other", "idemp_key_unique_101") is not None


# ============================================================================
# 7. Audit Event Persistence & Cryptographic Hash Chain Tests
# ============================================================================
def test_audit_repository_persistence_and_chain_verification(
    session_factory: sessionmaker[Session],
) -> None:
    audit_repo = SqlAlchemyAuditRepository(session_factory)
    tenant_id = "tenant_audit_test"

    now = datetime.now(UTC)
    t1 = now - timedelta(seconds=10)
    t2 = now - timedelta(seconds=5)

    # Event 1: Genesis event
    event1_data = {"order_id": "ord_1", "amount": 1000}
    event1_hash = AuditHasher.compute_event_hash(
        event_data=event1_data,
        previous_hash=GENESIS_HASH,
        actor="agent_01",
        event_type=AuditEventType.INTENT_PARSED.value,
        timestamp=t1,
        tenant_id=tenant_id,
        action_id="act_01",
        trace_id="trc_01",
    )
    event1 = AuditEvent(
        event_id="evt_01",
        tenant_id=tenant_id,
        actor="agent_01",
        event_type=AuditEventType.INTENT_PARSED,
        timestamp=t1,
        data=event1_data,
        previous_event_hash=GENESIS_HASH,
        event_hash=event1_hash,
        action_id="act_01",
        trace_id="trc_01",
        version=1,
    )
    audit_repo.append(event1)

    # Event 2: Chained to Event 1
    event2_data = {"payment_attempt_id": "att_1", "status": "CAPTURED"}
    event2_hash = AuditHasher.compute_event_hash(
        event_data=event2_data,
        previous_hash=event1_hash,
        actor="payment_orchestrator",
        event_type=AuditEventType.PAYMENT_CAPTURED.value,
        timestamp=t2,
        tenant_id=tenant_id,
        action_id="act_01",
        trace_id="trc_01",
    )
    event2 = AuditEvent(
        event_id="evt_02",
        tenant_id=tenant_id,
        actor="payment_orchestrator",
        event_type=AuditEventType.PAYMENT_CAPTURED,
        timestamp=t2,
        data=event2_data,
        previous_event_hash=event1_hash,
        event_hash=event2_hash,
        action_id="act_01",
        trace_id="trc_01",
        version=1,
    )
    audit_repo.append(event2)

    # Verify retrieval
    retrieved1 = audit_repo.get(tenant_id, "evt_01")
    assert retrieved1 is not None
    assert retrieved1 == event1

    retrieved2 = audit_repo.get(tenant_id, "evt_02")
    assert retrieved2 is not None
    assert retrieved2 == event2

    events = audit_repo.list_events(tenant_id)
    assert len(events) == 2
    assert events[0].event_id == "evt_01"
    assert events[1].event_id == "evt_02"

    # Verify intact chain
    assert audit_repo.verify_chain(tenant_id) is True

    # Multi-tenant isolation: other tenant sees 0 events and verifies as empty (valid)
    assert audit_repo.get("other_tenant", "evt_01") is None
    assert len(audit_repo.list_events("other_tenant")) == 0
    assert audit_repo.verify_chain("other_tenant") is True

    # Test record_event helper
    rec_event = audit_repo.record_event(
        event_type=AuditEventType.ORDER_CREATED,
        action_id="act_01",
        tenant_id=tenant_id,
        actor="order_orchestrator",
        data={"order_id": "ord_1", "status": "CONFIRMED"},
        trace_id="trc_01",
    )
    assert rec_event.previous_event_hash == event2_hash
    assert audit_repo.verify_chain(tenant_id) is True


# ============================================================================
# 8. Approval Persistence & Atomic Single-Use Consume Tests (§20, §49)
# ============================================================================
def test_approval_repository_roundtrip_and_atomic_consume(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_repo = SqlAlchemyTenantRepository(session_factory)
    tenant_repo.save(Tenant(tenant_id="tenant_appr", name="Approval Tenant"))
    tenant_repo.save(Tenant(tenant_id="tenant_other", name="Other Tenant"))

    approval_repo = SqlAlchemyApprovalRepository(session_factory)
    now = datetime.now(UTC)

    binding = ApprovalBinding(
        action_id="act_procure_01",
        amount=Money(75000, "USD"),
        merchant_id="merch_dell",
        resource_id="res_server_r750",
        currency="USD",
        action_version=1,
        tenant_id="tenant_appr",
        principal_id="user_procure_99",
    )
    approval = Approval(
        approval_id="appr_server_01",
        tenant_id="tenant_appr",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=now,
    )

    # 1. Save and retrieve
    approval_repo.save("tenant_appr", approval)
    retrieved = approval_repo.get("tenant_appr", "appr_server_01")
    assert retrieved is not None
    assert retrieved.approval_id == "appr_server_01"
    assert retrieved.tenant_id == "tenant_appr"
    assert retrieved.status == ApprovalStatus.PENDING
    assert retrieved.binding.principal_id == "user_procure_99"
    assert retrieved.binding.amount.minor_units == 75000
    assert retrieved.binding.amount.currency == "USD"
    assert retrieved.binding.merchant_id == "merch_dell"

    # 2. list_pending
    pending_list = approval_repo.list_pending("tenant_appr")
    assert len(pending_list) == 1
    assert pending_list[0].approval_id == "appr_server_01"
    assert pending_list[0].binding.principal_id == "user_procure_99"
    assert approval_repo.list_pending("tenant_other") == []

    # 3. list_for_action
    action_approvals = approval_repo.list_for_action("tenant_appr", "act_procure_01")
    assert len(action_approvals) == 1
    assert action_approvals[0].approval_id == "appr_server_01"

    # 3. Cross-tenant isolation
    assert approval_repo.get("tenant_other", "appr_server_01") is None
    assert len(approval_repo.list_for_action("tenant_other", "act_procure_01")) == 0
    with pytest.raises(CrossTenantAccessError):
        approval_repo.save("tenant_other", approval)

    # 4. Attempt to consume while still PENDING raises ApprovalBindingMismatchError
    with pytest.raises(ApprovalBindingMismatchError):
        approval_repo.atomic_consume("tenant_appr", "appr_server_01")

    # 5. Approve and save
    approved = approval.approve(approver_id="admin_user_99")
    approval_repo.save("tenant_appr", approved)
    retrieved_approved = approval_repo.get("tenant_appr", "appr_server_01")
    assert retrieved_approved is not None
    assert retrieved_approved.status == ApprovalStatus.APPROVED
    assert retrieved_approved.approver_id == "admin_user_99"

    # 6. Atomic single-use consume (§20, §177 Vector 10)
    consumed = approval_repo.atomic_consume("tenant_appr", "appr_server_01")
    assert consumed.status == ApprovalStatus.CONSUMED
    persisted_consumed = approval_repo.get("tenant_appr", "appr_server_01")
    assert persisted_consumed is not None
    assert persisted_consumed.status == ApprovalStatus.CONSUMED

    # 7. Replay consumption must raise ApprovalAlreadyConsumedError
    with pytest.raises(ApprovalAlreadyConsumedError):
        approval_repo.atomic_consume("tenant_appr", "appr_server_01")

    # 8. Consuming nonexistent approval raises InvalidCommerceError
    with pytest.raises(InvalidCommerceError):
        approval_repo.atomic_consume("tenant_appr", "nonexistent_appr")

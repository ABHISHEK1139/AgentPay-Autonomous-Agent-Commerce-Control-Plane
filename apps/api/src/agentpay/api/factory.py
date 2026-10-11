"""Application Factory for AgentPay ASGI API (§8, §13, §49).

Provides a clean `create_app` factory for instantiating the full AgentPay
API application with either in-memory adapters (for fast hermetic testing and development)
or SQLAlchemy 2.0 relational database repositories (for persistent deployments).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryEvidenceRepository,
    InMemoryInventoryRepository,
    InMemoryMerchantRepository,
    InMemoryProductRepository,
    InMemoryVariantRepository,
)
from agentpay.adapters.in_memory.identity_repository import (
    InMemoryAgentPermissionsRepository,
    InMemoryAgentRepository,
    InMemoryCredentialRepository,
    InMemoryTenantRepository,
)
from agentpay.adapters.in_memory.payment_repository import (
    InMemoryIdempotencyStore,
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
)
from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.api.app import AgentPayApiApp
from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.application.use_cases.purchase_flow import PurchaseOrchestrator
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Credential,
    CredentialStatus,
    CredentialType,
    Tenant,
    TenantStatus,
    UserRole,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import SpendingLimits
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.agent_repository import (
    SqlAlchemyAgentPermissionsRepository,
    SqlAlchemyAgentRepository,
)
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.audit_ledger import (
    SqlAlchemyAuditRepository,
)
from agentpay.infrastructure.database.repositories.commerce_repository import (
    SqlAlchemyEvidenceRepository,
    SqlAlchemyInventoryRepository,
    SqlAlchemyMerchantRepository,
    SqlAlchemyProductRepository,
    SqlAlchemyVariantRepository,
)
from agentpay.infrastructure.database.repositories.credential_repository import (
    SqlAlchemyCredentialRepository,
)
from agentpay.infrastructure.database.repositories.order_repository import (
    SqlAlchemyOrderRepository,
)
from agentpay.infrastructure.database.repositories.payment_repository import (
    SqlAlchemyIdempotencyStore,
    SqlAlchemyPaymentRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)
from agentpay.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)

logger = logging.getLogger(__name__)


def seed_initial_demo_data(
    tenant_id: str,
    tenant_repo: Any,
    agent_repo: Any,
    perms_repo: Any,
    merchant_repo: Any,
    product_repo: Any,
    variant_repo: Any,
    inventory_repo: Any,
    credential_repo: Any,
) -> None:
    """Seed baseline tenant, agent, merchant, products, and credentials for development (§112)."""
    now = datetime.now(UTC)

    # 1. Tenant
    tenant_repo.save(
        Tenant(
            tenant_id=tenant_id,
            name="Acme Autonomous Commerce",
            status=TenantStatus.ACTIVE,
        )
    )

    # 2. Agent
    agent = Agent(
        agent_id="agent_01",
        tenant_id=tenant_id,
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        scopes=("commerce:read", "commerce:write"),
        created_at=now,
        expires_at=now + timedelta(days=365),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save(tenant_id, agent)

    # 3. Agent Permissions
    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=2000000,  # ₹20,000 hard ceiling (§112 Scenario 3)
            daily_minor=5000000,  # ₹50,000 daily limit
            requires_approval_above_minor=200000,  # ₹2,000 autonomous threshold (§112 Scenario 2)
        ),
        allowed_categories=("electronics", "computers", "software", "accessories"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save(tenant_id, perms)

    # 4. Merchant
    merchant = Merchant(
        merchant_id="merch_01",
        tenant_id=tenant_id,
        name="Official Enterprise Hardware",
        status=MerchantStatus.ACTIVE,
    )
    merchant_repo.save(tenant_id, merchant)

    # 5. Products & Variants
    p1 = Product(
        product_id="prod_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Pro Laptop",
        category="computers",
        description="High-performance autonomous developer workstation",
    )
    product_repo.save(tenant_id, p1)

    v1 = ProductVariant(
        variant_id="var_01",
        product_id="prod_01",
        sku="SKU-PRO-LAPTOP-16",
        name="16GB Unified Memory / 512GB SSD",
        price=Money(129900, "INR"),  # ₹1,299.00 (under ₹2,000 autonomous threshold)
    )
    variant_repo.save(tenant_id, v1)

    p2 = Product(
        product_id="prod_02",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Enterprise Server Rack",
        category="computers",
        description="Dual socket enterprise compute cluster node",
    )
    product_repo.save(tenant_id, p2)

    v2 = ProductVariant(
        variant_id="var_02",
        product_id="prod_02",
        sku="SKU-ENT-SERVER-02",
        name="64 Core / 256GB RAM",
        price=Money(500000, "INR"),  # ₹5,000.00 (triggers human approval)
    )
    variant_repo.save(tenant_id, v2)

    # 6. Inventory
    inventory_repo.save(
        tenant_id,
        Inventory(variant_id="var_01", tenant_id=tenant_id, available=50, reserved=0),
    )
    inventory_repo.save(
        tenant_id,
        Inventory(variant_id="var_02", tenant_id=tenant_id, available=10, reserved=0),
    )

    # 7. Credentials
    exp = datetime(2099, 1, 1, tzinfo=UTC)

    # Admin Bearer Token (matches web control center default)
    admin_token = "test_bearer_admin_token"
    credential_repo.save(
        tenant_id,
        Credential(
            credential_id="cred_admin_01",
            tenant_id=tenant_id,
            principal_id="usr_admin_01",
            credential_type=CredentialType.BEARER_TOKEN,
            secret_hash=Credential.hash_secret(admin_token),
            expires_at=exp,
            status=CredentialStatus.ACTIVE,
            role=UserRole.ADMIN,
            scopes=("admin", "operator", "commerce:read", "commerce:write"),
        ),
    )

    # Agent API Key
    agent_key = "apk_test_agent_key_tenant_01"
    credential_repo.save(
        tenant_id,
        Credential(
            credential_id="cred_agent_01",
            tenant_id=tenant_id,
            principal_id="agent_01",
            credential_type=CredentialType.API_KEY,
            secret_hash=Credential.hash_secret(agent_key),
            expires_at=exp,
            status=CredentialStatus.ACTIVE,
            role=None,
            scopes=("agent", "commerce:read", "commerce:write"),
        ),
    )

    # Buyer API Key
    buyer_key = "apk_test_buyer_key_tenant_01"
    credential_repo.save(
        tenant_id,
        Credential(
            credential_id="cred_buyer_01",
            tenant_id=tenant_id,
            principal_id="usr_buyer_01",
            credential_type=CredentialType.API_KEY,
            secret_hash=Credential.hash_secret(buyer_key),
            expires_at=exp,
            status=CredentialStatus.ACTIVE,
            role=UserRole.BUYER,
            scopes=("commerce:read", "commerce:write"),
        ),
    )


def create_app(
    database_url: str | None = None,
    seed_demo_data: bool = True,
    tenant_id: str = "tenant_01",
    webhook_secret: str = "whsec_agentpay_default_secure_secret_32bytes",
) -> AgentPayApiApp:
    """Instantiate and wire a complete AgentPay application instance (§8, §13, §49).

    If `database_url` is provided (e.g. `sqlite:///agentpay.db` or PostgreSQL connection URI),
    relational SQLAlchemy 2.0 repositories and schemas will be initialized.
    Otherwise, thread-safe in-memory repository adapters will be used.
    """
    tenant_repo: Any
    agent_repo: Any
    perms_repo: Any
    credential_repo: Any
    merchant_repo: Any
    product_repo: Any
    variant_repo: Any
    inventory_repo: Any
    evidence_repo: Any
    approval_repo: Any
    payment_repo: Any
    order_repo: Any
    idempotency_store: Any
    audit_ledger: Any

    if database_url:
        engine = create_database_engine(database_url)
        Base.metadata.create_all(engine)
        session_factory = create_session_factory(engine)

        tenant_repo = SqlAlchemyTenantRepository(session_factory)
        agent_repo = SqlAlchemyAgentRepository(session_factory)
        perms_repo = SqlAlchemyAgentPermissionsRepository(session_factory)
        credential_repo = SqlAlchemyCredentialRepository(session_factory)
        merchant_repo = SqlAlchemyMerchantRepository(session_factory)
        product_repo = SqlAlchemyProductRepository(session_factory)
        variant_repo = SqlAlchemyVariantRepository(session_factory)
        inventory_repo = SqlAlchemyInventoryRepository(session_factory)
        evidence_repo = SqlAlchemyEvidenceRepository(session_factory)
        approval_repo = SqlAlchemyApprovalRepository(session_factory)
        payment_repo = SqlAlchemyPaymentRepository(session_factory)
        order_repo = SqlAlchemyOrderRepository(session_factory)
        idempotency_store = SqlAlchemyIdempotencyStore(session_factory)
        audit_ledger = SqlAlchemyAuditRepository(session_factory)
    else:
        tenant_repo = InMemoryTenantRepository()
        agent_repo = InMemoryAgentRepository()
        perms_repo = InMemoryAgentPermissionsRepository()
        credential_repo = InMemoryCredentialRepository()
        merchant_repo = InMemoryMerchantRepository()
        product_repo = InMemoryProductRepository()
        variant_repo = InMemoryVariantRepository()
        inventory_repo = InMemoryInventoryRepository()
        evidence_repo = InMemoryEvidenceRepository()
        approval_repo = InMemoryApprovalRepository()
        payment_repo = InMemoryPaymentRepository()
        order_repo = InMemoryOrderRepository()
        idempotency_store = InMemoryIdempotencyStore()
        audit_ledger = InMemoryAuditLedger()

    payment_provider = MockPaymentProvider()
    decision_engine = MockDecisionEngine()

    auth_service = AgentAuthorizationService(
        tenant_repo=tenant_repo,
        agent_repo=agent_repo,
        permissions_repo=perms_repo,
    )
    approval_service = ApprovalService(approval_repo=approval_repo)

    orchestrator = PurchaseOrchestrator(
        tenant_repo=tenant_repo,
        agent_repo=agent_repo,
        permissions_repo=perms_repo,
        auth_service=auth_service,
        merchant_repo=merchant_repo,
        product_repo=product_repo,
        variant_repo=variant_repo,
        inventory_repo=inventory_repo,
        evidence_repo=evidence_repo,
        decision_engine=decision_engine,
        approval_service=approval_service,
        approval_repo=approval_repo,
        payment_provider=payment_provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
        idempotency_store=idempotency_store,
        audit_ledger=audit_ledger,
    )

    if seed_demo_data:
        seed_initial_demo_data(
            tenant_id=tenant_id,
            tenant_repo=tenant_repo,
            agent_repo=agent_repo,
            perms_repo=perms_repo,
            merchant_repo=merchant_repo,
            product_repo=product_repo,
            variant_repo=variant_repo,
            inventory_repo=inventory_repo,
            credential_repo=credential_repo,
        )

    app = AgentPayApiApp(
        orchestrator=orchestrator,
        credential_repo=credential_repo,
        idempotency_store=idempotency_store,
        webhook_secret=webhook_secret,
    )
    return app

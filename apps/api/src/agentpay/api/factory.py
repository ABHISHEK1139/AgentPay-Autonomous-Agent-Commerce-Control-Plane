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
from agentpay.adapters.in_memory.emergency_pause_store import (
    InMemoryEmergencyPauseStore,
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
    InMemoryProviderWebhookEventRepository,
)
from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.adapters.model_gateway.model_gateway import ModelGateway
from agentpay.adapters.razorpay_payment_provider import InMemoryRazorpaySimulator
from agentpay.adapters.razorpay_sandbox_provider import RazorpaySandboxProvider
from agentpay.adapters.stripe_payment_provider import InMemoryStripeSimulator
from agentpay.api.app import AgentPayApiApp
from agentpay.application.ports.approval_port import DecisionEngine
from agentpay.application.ports.payment_port import PaymentProvider
from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.application.use_cases.purchase_flow import PurchaseOrchestrator
from agentpay.config.config import (
    AppConfig,
    ConfigValidationError,
    DecisionEngineType,
    Environment,
    PaymentProviderType,
)
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
from agentpay.infrastructure.database.repositories.emergency_pause_repository import (
    SqlAlchemyEmergencyPauseRepository,
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
from agentpay.infrastructure.database.repositories.webhook_event_repository import (
    SqlAlchemyProviderWebhookEventRepository,
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


def _verify_database_schema_ready(engine: Any) -> None:
    """Verify that required database tables exist in sandbox/production before application boot (§49, AP-007).

    Deployments must run migrations explicitly (e.g. `alembic upgrade head` or `agentpay migrate`).
    Fails fast if migrations have not been applied.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    required_tables = {
        "tenants",
        "agents",
        "agent_permissions",
        "credentials",
        "merchants",
        "products",
        "product_variants",
        "inventory",
        "approvals",
        "payment_attempts",
        "orders",
        "idempotency_records",
        "audit_events",
        "provider_webhook_events",
        "provider_transaction_mappings",
        "emergency_pause_records",
    }
    missing = required_tables - existing_tables
    if missing or "alembic_version" not in existing_tables:
        missing_desc = sorted(missing or {"alembic_version"})
        raise ConfigValidationError(
            f"Database schema is not ready for environment. Missing required tables: {missing_desc}. "
            "Run 'agentpay migrate' or 'alembic upgrade head' before starting in production or sandbox mode (§49, AP-007)."
        )


def create_app(
    config_or_db: AppConfig | str | None = None,
    *,
    config: AppConfig | None = None,
    database_url: str | None = None,
    seed_demo_data: bool | None = None,
    tenant_id: str | None = None,
    webhook_secret: str | None = None,
    require_observability_auth: bool | None = None,
    payment_provider: PaymentProvider | None = None,
    decision_engine: DecisionEngine | None = None,
) -> AgentPayApiApp:
    """Instantiate and wire a complete AgentPay application instance (§8, §13, §49, AP-005).

    Resolves validated immutable AppConfig, dynamically constructs configured payment provider
    and decision engine, and wires persistence repositories.
    """
    # 1. Resolve configuration
    if isinstance(config_or_db, AppConfig):
        cfg = config_or_db
    elif config is not None:
        cfg = config
    else:
        cfg = AppConfig.from_env()

    overrides: dict[str, Any] = {}
    if isinstance(config_or_db, str):
        overrides["database_url"] = config_or_db
    if database_url is not None:
        overrides["database_url"] = database_url
    if seed_demo_data is not None:
        overrides["seed_demo_data"] = seed_demo_data
    if tenant_id is not None:
        overrides["tenant_id"] = tenant_id
    if webhook_secret is not None:
        overrides["razorpay_webhook_secret"] = webhook_secret
        overrides["stripe_webhook_secret"] = webhook_secret
    if require_observability_auth is not None:
        overrides["require_observability_auth"] = require_observability_auth

    if overrides:
        cfg = cfg.with_overrides(**overrides)

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
    webhook_event_repo: Any
    emergency_pause_store: Any

    if cfg.database_url:
        engine = create_database_engine(cfg.database_url)
        if cfg.is_production_like:
            _verify_database_schema_ready(engine)
        else:
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
        webhook_event_repo = SqlAlchemyProviderWebhookEventRepository(session_factory)
        emergency_pause_store = SqlAlchemyEmergencyPauseRepository(session_factory)
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
        webhook_event_repo = InMemoryProviderWebhookEventRepository()
        emergency_pause_store = InMemoryEmergencyPauseStore()

    # 2. Dynamically wire PaymentProvider
    if payment_provider is None:
        if cfg.payment_provider == PaymentProviderType.MOCK:
            payment_provider = MockPaymentProvider()
        elif cfg.payment_provider in (
            PaymentProviderType.IN_MEMORY_STRIPE,
            PaymentProviderType.STRIPE,
        ):
            payment_provider = InMemoryStripeSimulator(
                api_key=cfg.stripe_api_key or "sk_test_placeholder_key_for_testing",
                webhook_secret=cfg.stripe_webhook_secret,
            )
        elif cfg.payment_provider == PaymentProviderType.RAZORPAY_SANDBOX or (
            cfg.environment == Environment.SANDBOX
            and cfg.payment_provider == PaymentProviderType.RAZORPAY
        ):
            payment_provider = RazorpaySandboxProvider(
                key_id=cfg.razorpay_key_id or "",
                key_secret=cfg.razorpay_key_secret or "",
                webhook_secret=cfg.razorpay_webhook_secret,
            )
        elif cfg.payment_provider in (
            PaymentProviderType.IN_MEMORY_RAZORPAY,
            PaymentProviderType.RAZORPAY,
        ):
            payment_provider = InMemoryRazorpaySimulator(
                key_id=cfg.razorpay_key_id or "rzp_test_placeholder_id",
                key_secret=cfg.razorpay_key_secret or "rzp_test_placeholder_secret",
                webhook_secret=cfg.razorpay_webhook_secret,
            )
        else:
            raise ConfigValidationError(f"Unsupported payment provider: {cfg.payment_provider}")

    # 3. Dynamically wire DecisionEngine
    if decision_engine is None:
        if cfg.decision_engine == DecisionEngineType.MOCK:
            decision_engine = MockDecisionEngine()
        elif cfg.decision_engine == DecisionEngineType.MODEL_GATEWAY:
            decision_engine = ModelGateway()
        else:
            raise ConfigValidationError(f"Unsupported decision engine: {cfg.decision_engine}")

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

    if cfg.seed_demo_data:
        seed_initial_demo_data(
            tenant_id=cfg.tenant_id,
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
        webhook_event_repo=webhook_event_repo,
        emergency_pause_store=emergency_pause_store,
        webhook_secret=cfg.razorpay_webhook_secret,
        require_observability_auth=cfg.require_observability_auth,
        config=cfg,
    )
    return app

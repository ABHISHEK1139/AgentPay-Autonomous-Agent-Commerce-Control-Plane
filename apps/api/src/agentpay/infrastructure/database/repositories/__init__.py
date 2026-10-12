"""SQLAlchemy Repositories for AgentPay Foundation (§49)."""

from __future__ import annotations

from agentpay.infrastructure.database.repositories.agent_repository import (
    SqlAlchemyAgentPermissionsRepository,
    SqlAlchemyAgentRepository,
)
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.audit_ledger import (
    SqlAlchemyAuditLedger,
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
from agentpay.infrastructure.database.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)
from agentpay.infrastructure.database.repositories.payment_repository import (
    SqlAlchemyIdempotencyStore,
    SqlAlchemyPaymentRepository,
)
from agentpay.infrastructure.database.repositories.provider_mapping_repository import (
    SqlAlchemyProviderTransactionMappingRepository,
)
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)
from agentpay.infrastructure.database.repositories.webhook_event_repository import (
    SqlAlchemyProviderWebhookEventRepository,
)

__all__ = [
    "SqlAlchemyAgentPermissionsRepository",
    "SqlAlchemyAgentRepository",
    "SqlAlchemyApprovalRepository",
    "SqlAlchemyAuditLedger",
    "SqlAlchemyAuditRepository",
    "SqlAlchemyCredentialRepository",
    "SqlAlchemyEmergencyPauseRepository",
    "SqlAlchemyEvidenceRepository",
    "SqlAlchemyIdempotencyStore",
    "SqlAlchemyInventoryRepository",
    "SqlAlchemyMerchantRepository",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyOutboxRepository",
    "SqlAlchemyPaymentRepository",
    "SqlAlchemyProductRepository",
    "SqlAlchemyProviderTransactionMappingRepository",
    "SqlAlchemyProviderWebhookEventRepository",
    "SqlAlchemyTenantRepository",
    "SqlAlchemyVariantRepository",
]

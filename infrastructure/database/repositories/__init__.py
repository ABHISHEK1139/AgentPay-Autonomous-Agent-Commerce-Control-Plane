"""Root database repositories re-export (§9, §49)."""

from __future__ import annotations

from agentpay.infrastructure.database.repositories import (
    SqlAlchemyAgentPermissionsRepository,
    SqlAlchemyAgentRepository,
    SqlAlchemyApprovalRepository,
    SqlAlchemyAuditLedger,
    SqlAlchemyAuditRepository,
    SqlAlchemyCredentialRepository,
    SqlAlchemyEvidenceRepository,
    SqlAlchemyIdempotencyStore,
    SqlAlchemyInventoryRepository,
    SqlAlchemyMerchantRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPaymentRepository,
    SqlAlchemyProductRepository,
    SqlAlchemyTenantRepository,
    SqlAlchemyVariantRepository,
)

__all__ = [
    "SqlAlchemyAgentPermissionsRepository",
    "SqlAlchemyAgentRepository",
    "SqlAlchemyApprovalRepository",
    "SqlAlchemyAuditLedger",
    "SqlAlchemyAuditRepository",
    "SqlAlchemyCredentialRepository",
    "SqlAlchemyEvidenceRepository",
    "SqlAlchemyIdempotencyStore",
    "SqlAlchemyInventoryRepository",
    "SqlAlchemyMerchantRepository",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyPaymentRepository",
    "SqlAlchemyProductRepository",
    "SqlAlchemyTenantRepository",
    "SqlAlchemyVariantRepository",
]

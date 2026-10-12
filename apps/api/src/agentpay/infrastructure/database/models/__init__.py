"""SQLAlchemy 2.0 Relational Models for AgentPay Foundation (§49)."""

from __future__ import annotations

from agentpay.infrastructure.database.base import Base, TenantScopedMixin, TimestampMixin
from agentpay.infrastructure.database.models.agent import AgentModel
from agentpay.infrastructure.database.models.approval import ApprovalModel
from agentpay.infrastructure.database.models.audit import AuditEventModel
from agentpay.infrastructure.database.models.commerce import (
    EvidenceModel,
    InventoryModel,
    MerchantModel,
    ProductModel,
    ProductVariantModel,
)
from agentpay.infrastructure.database.models.credential import CredentialModel
from agentpay.infrastructure.database.models.emergency_pause import (
    EmergencyPauseRecordModel,
)
from agentpay.infrastructure.database.models.identity import AgentPermissionsModel
from agentpay.infrastructure.database.models.order import OrderModel
from agentpay.infrastructure.database.models.outbox import OutboxEventModel
from agentpay.infrastructure.database.models.payment import PaymentAttemptModel
from agentpay.infrastructure.database.models.payments import (
    IdempotencyRecordModel,
    RefundModel,
)
from agentpay.infrastructure.database.models.provider_mapping import (
    ProviderTransactionMappingModel,
)
from agentpay.infrastructure.database.models.provider_webhook_event import (
    ProviderWebhookEventModel,
)
from agentpay.infrastructure.database.models.tenant import TenantModel, UserModel

__all__ = [
    "AgentModel",
    "AgentPermissionsModel",
    "ApprovalModel",
    "AuditEventModel",
    "Base",
    "CredentialModel",
    "EmergencyPauseRecordModel",
    "EvidenceModel",
    "IdempotencyRecordModel",
    "InventoryModel",
    "MerchantModel",
    "OrderModel",
    "OutboxEventModel",
    "PaymentAttemptModel",
    "ProductModel",
    "ProductVariantModel",
    "ProviderTransactionMappingModel",
    "ProviderWebhookEventModel",
    "RefundModel",
    "TenantModel",
    "TenantScopedMixin",
    "TimestampMixin",
    "UserModel",
]

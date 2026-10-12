"""Relational model for ProviderTransactionMapping entity (§39, §41, §49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.payments import ProviderTransactionMapping
from agentpay.infrastructure.database.base import Base


class ProviderTransactionMappingModel(Base):
    """SQLAlchemy model for ProviderTransactionMapping entity (§39, §41, §49)."""

    __tablename__ = "provider_transaction_mappings"

    mapping_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_provider_tx_payment"),
        Index("ix_provider_tx_lookup", "provider", "provider_payment_id", "provider_account_id"),
    )

    def to_domain(self) -> ProviderTransactionMapping:
        """Convert relational model instance to pure domain ProviderTransactionMapping dataclass."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return ProviderTransactionMapping(
            mapping_id=self.mapping_id,
            tenant_id=self.tenant_id,
            attempt_id=self.attempt_id,
            provider=self.provider,
            provider_payment_id=self.provider_payment_id,
            provider_order_id=self.provider_order_id,
            provider_account_id=self.provider_account_id,
            order_id=self.order_id,
            created_at=created_at,
        )

    @classmethod
    def from_domain(cls, mapping: ProviderTransactionMapping) -> ProviderTransactionMappingModel:
        """Create relational model from pure domain entity."""
        return cls(
            mapping_id=mapping.mapping_id,
            tenant_id=mapping.tenant_id,
            attempt_id=mapping.attempt_id,
            provider=mapping.provider,
            provider_payment_id=mapping.provider_payment_id,
            provider_order_id=mapping.provider_order_id,
            provider_account_id=mapping.provider_account_id,
            order_id=mapping.order_id,
            created_at=mapping.created_at,
        )

"""Relational model for ProviderWebhookEvent entity (§42, §49, §61, AP-003)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.payments import ProviderWebhookEvent, WebhookEventStatus
from agentpay.infrastructure.database.base import Base


class ProviderWebhookEventModel(Base):
    """SQLAlchemy model for provider_webhook_events table (§42, §49, §61, AP-003)."""

    __tablename__ = "provider_webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_account_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default="", index=True
    )
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=WebhookEventStatus.RECEIVED.value,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_account_id",
            "event_id",
            name="uq_provider_webhook_events_provider_account_event",
        ),
        Index(
            "ix_provider_webhook_events_provider_account_event",
            "provider",
            "provider_account_id",
            "event_id",
        ),
        Index("ix_provider_webhook_status_received", "status", "received_at"),
    )

    def to_domain(self) -> ProviderWebhookEvent:
        """Convert relational model instance to pure domain ProviderWebhookEvent dataclass."""
        received_at = self.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)

        updated_at = self.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        return ProviderWebhookEvent(
            id=self.id,
            provider=self.provider,
            provider_account_id=self.provider_account_id or "",
            event_id=self.event_id,
            event_type=self.event_type,
            received_at=received_at,
            status=WebhookEventStatus(self.status),
            attempt_count=self.attempt_count,
            last_error_code=self.last_error_code,
            entity_reference=self.entity_reference,
            payload_hash=self.payload_hash,
            trace_id=self.trace_id,
            updated_at=updated_at,
        )

    @classmethod
    def from_domain(cls, event: ProviderWebhookEvent) -> ProviderWebhookEventModel:
        """Create relational model from pure domain entity."""
        return cls(
            id=event.id,
            provider=event.provider,
            provider_account_id=event.provider_account_id or "",
            event_id=event.event_id,
            event_type=event.event_type,
            received_at=event.received_at,
            status=event.status.value,
            attempt_count=event.attempt_count,
            last_error_code=event.last_error_code,
            entity_reference=event.entity_reference,
            payload_hash=event.payload_hash,
            trace_id=event.trace_id,
            updated_at=event.updated_at,
        )

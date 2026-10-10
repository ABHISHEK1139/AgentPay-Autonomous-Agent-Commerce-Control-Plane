"""Relational model for OutboxEvent entity (§49, §106)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.outbox import OutboxEvent, OutboxStatus
from agentpay.infrastructure.database.base import Base


class OutboxEventModel(Base):
    """SQLAlchemy 2.0 model for OutboxEvent entity (§49, §106)."""

    __tablename__ = "outbox_events"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OutboxStatus.PENDING.value, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_outbox_events_tenant_status", "tenant_id", "status"),
        Index("ix_outbox_events_created", "created_at"),
    )

    def to_domain(self) -> OutboxEvent:
        """Convert relational model instance to pure domain OutboxEvent dataclass."""
        cat = self.created_at
        if cat.tzinfo is None:
            cat = cat.replace(tzinfo=UTC)

        dat = self.dispatched_at
        if dat is not None and dat.tzinfo is None:
            dat = dat.replace(tzinfo=UTC)

        raw_payload: Any = self.payload
        payload_dict = dict(raw_payload) if isinstance(raw_payload, dict) else {}

        return OutboxEvent(
            tenant_id=self.tenant_id,
            event_id=self.event_id,
            event_type=self.event_type,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            payload=payload_dict,
            status=OutboxStatus(self.status),
            retry_count=self.retry_count,
            max_retries=self.max_retries,
            created_at=cat,
            dispatched_at=dat,
            error_message=self.error_message,
        )

    @classmethod
    def from_domain(cls, event: OutboxEvent) -> OutboxEventModel:
        """Create relational model instance from pure domain OutboxEvent dataclass."""
        st = event.status.value if hasattr(event.status, "value") else str(event.status)
        return cls(
            tenant_id=event.tenant_id,
            event_id=event.event_id,
            event_type=event.event_type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            payload=dict(event.payload),
            status=st,
            retry_count=event.retry_count,
            max_retries=event.max_retries,
            created_at=event.created_at,
            dispatched_at=event.dispatched_at,
            error_message=event.error_message,
        )

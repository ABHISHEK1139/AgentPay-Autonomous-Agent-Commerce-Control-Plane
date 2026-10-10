"""Relational model for AuditEvent entity (§45, §49)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.audit import AuditEvent, AuditEventType
from agentpay.infrastructure.database.base import Base


class AuditEventModel(Base):
    """SQLAlchemy model for AuditEvent entity (§45, §49)."""

    __tablename__ = "audit_events"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    previous_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        Index("ix_audit_events_tenant_timestamp", "tenant_id", "timestamp"),
        Index("ix_audit_events_tenant_action", "tenant_id", "action_id"),
    )

    def to_domain(self) -> AuditEvent:
        """Convert relational model instance to pure domain AuditEvent dataclass."""
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        raw_data: Any = self.data
        data_dict = dict(raw_data) if isinstance(raw_data, dict) else {}

        return AuditEvent(
            event_id=self.event_id,
            tenant_id=self.tenant_id,
            actor=self.actor,
            event_type=AuditEventType(self.event_type),
            timestamp=ts,
            data=data_dict,
            previous_event_hash=self.previous_event_hash,
            event_hash=self.event_hash,
            agent_id=self.agent_id,
            action_id=self.action_id,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            version=self.version,
        )

    @classmethod
    def from_domain(cls, event: AuditEvent) -> AuditEventModel:
        """Create relational model instance from pure domain AuditEvent dataclass."""
        etype = (
            event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        )
        return cls(
            tenant_id=event.tenant_id,
            event_id=event.event_id,
            actor=event.actor,
            event_type=etype,
            timestamp=event.timestamp,
            data=dict(event.data),
            previous_event_hash=event.previous_event_hash,
            event_hash=event.event_hash,
            agent_id=event.agent_id,
            action_id=event.action_id,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
            trace_id=event.trace_id,
            version=event.version,
        )

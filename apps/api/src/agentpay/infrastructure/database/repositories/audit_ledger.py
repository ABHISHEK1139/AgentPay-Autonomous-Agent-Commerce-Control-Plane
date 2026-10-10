"""SQLAlchemy implementation of AuditLedger and AuditRepository (§45, §46, §49, §50)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.audit_port import AuditLedger
from agentpay.domain.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    AuditHasher,
    SensitiveFieldRedactor,
)
from agentpay.domain.errors import InvalidAuditEventError
from agentpay.infrastructure.database.models.audit import AuditEventModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyAuditRepository(BaseSqlAlchemyRepository, AuditLedger):
    """SQLAlchemy-backed audit repository with cryptographic hash chaining and field redaction."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def append(self, event: AuditEvent) -> None:
        """Append an audit event directly to the repository."""
        with self._session() as session:
            session.add(AuditEventModel.from_domain(event))
            session.commit()

    def get(self, tenant_id: str, event_id: str) -> AuditEvent | None:
        """Retrieve an audit event by tenant_id and event_id."""
        with self._session() as session:
            stmt = select(AuditEventModel).where(
                AuditEventModel.tenant_id == tenant_id,
                AuditEventModel.event_id == event_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def list_events(self, tenant_id: str) -> list[AuditEvent]:
        """List all audit events for a tenant ordered by timestamp and event_id."""
        with self._session() as session:
            stmt = (
                select(AuditEventModel)
                .where(AuditEventModel.tenant_id == tenant_id)
                .order_by(AuditEventModel.timestamp.asc(), AuditEventModel.event_id.asc())
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def record_event(
        self,
        event_type: AuditEventType | str,
        action_id: str | None,
        tenant_id: str,
        actor: str,
        data: dict[str, Any],
        agent_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuditEvent:
        tenant_id_val: object = tenant_id
        if not isinstance(tenant_id_val, str) or not tenant_id_val.strip():
            raise InvalidAuditEventError("tenant_id must be a non-empty string")
        actor_val: object = actor
        if not isinstance(actor_val, str) or not actor_val.strip():
            raise InvalidAuditEventError("actor must be a non-empty string")

        resolved_type = AuditEventType(event_type)
        redacted_data = SensitiveFieldRedactor.redact(data)
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)

        with self._session() as session:
            stmt = (
                select(AuditEventModel)
                .where(AuditEventModel.tenant_id == tenant_id)
                .order_by(AuditEventModel.timestamp.desc(), AuditEventModel.event_id.desc())
                .limit(1)
            )
            latest = session.scalar(stmt)
            previous_hash = latest.event_hash if latest is not None else GENESIS_HASH

            event_hash = AuditHasher.compute_event_hash(
                event_data=redacted_data,
                previous_hash=previous_hash,
                actor=actor,
                event_type=resolved_type.value,
                timestamp=now,
                tenant_id=tenant_id,
                agent_id=agent_id,
                action_id=action_id,
                trace_id=trace_id,
            )

            event = AuditEvent(
                event_id=event_id,
                tenant_id=tenant_id,
                actor=actor,
                event_type=resolved_type,
                timestamp=now,
                data=redacted_data,
                previous_event_hash=previous_hash,
                event_hash=event_hash,
                agent_id=agent_id,
                action_id=action_id,
                request_id=request_id,
                correlation_id=correlation_id,
                trace_id=trace_id,
                version=1,
            )
            session.add(AuditEventModel.from_domain(event))
            session.commit()
            return event

    def get_events(
        self,
        tenant_id: str,
        action_id: str | None = None,
    ) -> list[AuditEvent]:
        with self._session() as session:
            stmt = (
                select(AuditEventModel)
                .where(AuditEventModel.tenant_id == tenant_id)
                .order_by(AuditEventModel.timestamp.asc(), AuditEventModel.event_id.asc())
            )
            if action_id is not None:
                stmt = stmt.where(AuditEventModel.action_id == action_id)

            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def verify_chain(self, tenant_id: str) -> bool:
        events = self.list_events(tenant_id)
        if not events:
            return True

        for idx, event in enumerate(events):
            expected_prev = GENESIS_HASH if idx == 0 else events[idx - 1].event_hash
            if event.previous_event_hash != expected_prev:
                return False

            recomputed = AuditHasher.compute_event_hash(
                event_data=event.data,
                previous_hash=event.previous_event_hash,
                actor=event.actor,
                event_type=event.event_type.value,
                timestamp=event.timestamp,
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                action_id=event.action_id,
                trace_id=event.trace_id,
            )
            if event.event_hash != recomputed:
                return False

        return True


# Alias for backward compatibility
SqlAlchemyAuditLedger = SqlAlchemyAuditRepository

__all__ = ["SqlAlchemyAuditLedger", "SqlAlchemyAuditRepository"]

"""SQLAlchemy implementation of OutboxRepository (§49, §50, §106)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.outbox import OutboxEvent, OutboxStatus
from agentpay.infrastructure.database.models.outbox import OutboxEventModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyOutboxRepository(BaseSqlAlchemyRepository):
    """SQLAlchemy 2.0 repository for OutboxEvent entities with tenant isolation."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, event_id: str) -> OutboxEvent | None:
        """Retrieves an outbox event scoped by tenant_id."""
        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.event_id == event_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id: str, event: OutboxEvent) -> None:
        """Saves or updates an outbox event, verifying tenant match."""
        if event.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"OutboxEvent tenant '{event.tenant_id}' does not match context '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.event_id == event.event_id,
            )
            model = session.scalar(stmt)
            if model is None:
                model = OutboxEventModel.from_domain(event)
                session.add(model)
            else:
                model.status = (
                    event.status.value if hasattr(event.status, "value") else str(event.status)
                )
                model.retry_count = event.retry_count
                model.dispatched_at = event.dispatched_at
                model.error_message = event.error_message
            session.commit()

    def list_pending(self, tenant_id: str | None = None, limit: int = 100) -> list[OutboxEvent]:
        """Lists pending outbox events ordered by created_at."""
        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.status == OutboxStatus.PENDING.value
            )
            if tenant_id is not None:
                stmt = stmt.where(OutboxEventModel.tenant_id == tenant_id)
            stmt = stmt.order_by(OutboxEventModel.created_at).limit(limit)
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def mark_dispatched(self, tenant_id: str, event_id: str) -> None:
        """Marks an outbox event as DISPATCHED."""
        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.event_id == event_id,
            )
            model = session.scalar(stmt)
            if model:
                model.status = OutboxStatus.DISPATCHED.value
                model.dispatched_at = datetime.now(UTC)
                model.error_message = None
                session.commit()

    def record_failure(self, tenant_id: str, event_id: str, error: str) -> None:
        """Records failure and increments retry counter."""
        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.event_id == event_id,
            )
            model = session.scalar(stmt)
            if model:
                model.retry_count += 1
                model.error_message = error
                if model.retry_count >= model.max_retries:
                    model.status = OutboxStatus.FAILED.value
                else:
                    model.status = OutboxStatus.PENDING.value
                session.commit()

    def count(self, tenant_id: str | None = None, status: OutboxStatus | None = None) -> int:
        """Counts outbox events matching criteria."""
        with self._session() as session:
            stmt = select(func.count(OutboxEventModel.event_id))
            if tenant_id is not None:
                stmt = stmt.where(OutboxEventModel.tenant_id == tenant_id)
            if status is not None:
                stmt = stmt.where(OutboxEventModel.status == status.value)
            res = session.scalar(stmt)
            return int(res or 0)

"""SQLAlchemy implementation of OutboxRepository (§49, §50, §106)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.outbox import OutboxEvent, OutboxStatus
from agentpay.infrastructure.database.models.outbox import OutboxEventModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyOutboxRepository(BaseSqlAlchemyRepository):
    """SQLAlchemy 2.0 repository for OutboxEvent entities with tenant isolation (§49, §50, §106)."""

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
                model.locked_by = event.locked_by
                model.locked_until = event.locked_until
                model.next_retry_at = event.next_retry_at
            session.commit()

    def claim_pending(
        self,
        worker_id: str,
        lease_seconds: int = 30,
        tenant_id: str | None = None,
        limit: int = 50,
        at: datetime | None = None,
    ) -> list[OutboxEvent]:
        """Atomically claims eligible pending/stale outbox events under a worker lease (§106, B-OUT-01)."""
        now = at or datetime.now(UTC)
        with self._session() as session:
            cond_pending = (OutboxEventModel.status == OutboxStatus.PENDING.value) & (
                (OutboxEventModel.next_retry_at.is_(None)) | (OutboxEventModel.next_retry_at <= now)
            )
            cond_expired_lease = (
                (OutboxEventModel.status == OutboxStatus.PROCESSING.value)
                & (OutboxEventModel.locked_until.is_not(None))
                & (OutboxEventModel.locked_until < now)
            )

            stmt = select(OutboxEventModel).where(cond_pending | cond_expired_lease)
            if tenant_id is not None:
                stmt = stmt.where(OutboxEventModel.tenant_id == tenant_id)
            stmt = stmt.order_by(OutboxEventModel.created_at).limit(limit)

            models = session.scalars(stmt).all()
            claimed_events: list[OutboxEvent] = []
            for model in models:
                model.status = OutboxStatus.PROCESSING.value
                model.locked_by = worker_id
                model.locked_until = now + timedelta(seconds=lease_seconds)
                claimed_events.append(model.to_domain())
            session.commit()
            return claimed_events

    def list_pending(self, tenant_id: str | None = None, limit: int = 100) -> list[OutboxEvent]:
        """Lists pending outbox events ordered by created_at."""
        now = datetime.now(UTC)
        with self._session() as session:
            cond_pending = (OutboxEventModel.status == OutboxStatus.PENDING.value) & (
                (OutboxEventModel.next_retry_at.is_(None)) | (OutboxEventModel.next_retry_at <= now)
            )
            cond_expired_lease = (
                (OutboxEventModel.status == OutboxStatus.PROCESSING.value)
                & (OutboxEventModel.locked_until.is_not(None))
                & (OutboxEventModel.locked_until < now)
            )

            stmt = select(OutboxEventModel).where(cond_pending | cond_expired_lease)
            if tenant_id is not None:
                stmt = stmt.where(OutboxEventModel.tenant_id == tenant_id)
            stmt = stmt.order_by(OutboxEventModel.created_at).limit(limit)
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def mark_dispatched(self, tenant_id: str, event_id: str) -> None:
        """Marks an outbox event as DISPATCHED and releases lease."""
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
                model.locked_by = None
                model.locked_until = None
                session.commit()

    def record_failure(
        self,
        tenant_id: str,
        event_id: str,
        error: str,
        backoff_base_seconds: float = 2.0,
        at: datetime | None = None,
    ) -> None:
        """Records failure, computes exponential backoff, and releases lease."""
        now = at or datetime.now(UTC)
        with self._session() as session:
            stmt = select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant_id,
                OutboxEventModel.event_id == event_id,
            )
            model = session.scalar(stmt)
            if model:
                model.retry_count += 1
                model.error_message = error
                model.locked_by = None
                model.locked_until = None
                if model.retry_count >= model.max_retries:
                    model.status = OutboxStatus.FAILED.value
                    model.next_retry_at = None
                else:
                    model.status = OutboxStatus.PENDING.value
                    delay = (2.0 ** (model.retry_count - 1)) * backoff_base_seconds
                    model.next_retry_at = now + timedelta(seconds=delay)
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

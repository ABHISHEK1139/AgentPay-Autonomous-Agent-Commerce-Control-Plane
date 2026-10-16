"""SQLAlchemy implementation of PaymentRepository and IdempotencyStore ports (§39, §41, §42, §49, §50)."""

from __future__ import annotations

import json
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.payment_port import (
    IdempotencyStore,
    PaymentRepository,
    RefundRepository,
)
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.money import Money
from agentpay.domain.payments import IdempotencyRecord, PaymentAttempt, Refund
from agentpay.infrastructure.database.models.payment import PaymentAttemptModel
from agentpay.infrastructure.database.models.payments import (
    IdempotencyRecordModel,
    RefundModel,
)
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository
from agentpay.infrastructure.database.repositories.order_repository import (
    SqlAlchemyOrderRepository,
)


class SqlAlchemyPaymentRepository(BaseSqlAlchemyRepository, PaymentRepository):
    """SQLAlchemy-backed repository for PaymentAttempt persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, attempt_id: str) -> PaymentAttempt | None:
        with self._session() as session:
            stmt = select(PaymentAttemptModel).where(
                PaymentAttemptModel.tenant_id == tenant_id,
                PaymentAttemptModel.attempt_id == attempt_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def get_by_idempotency_key(self, tenant_id: str, key: str) -> PaymentAttempt | None:
        with self._session() as session:
            stmt = select(PaymentAttemptModel).where(
                PaymentAttemptModel.tenant_id == tenant_id,
                PaymentAttemptModel.idempotency_key == key,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(
        self,
        tenant_id_or_attempt: str | PaymentAttempt,
        attempt: PaymentAttempt | None = None,
    ) -> None:
        """Persist or update a payment attempt entity.

        Supports both save(attempt) and save(tenant_id, attempt).
        """
        if isinstance(tenant_id_or_attempt, PaymentAttempt):
            target_attempt = tenant_id_or_attempt
            tenant_id = target_attempt.tenant_id
        else:
            tenant_id = tenant_id_or_attempt
            if attempt is None:
                raise ValueError("PaymentAttempt entity must be provided")
            target_attempt = attempt

        if target_attempt.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"PaymentAttempt tenant '{target_attempt.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(PaymentAttemptModel).where(
                PaymentAttemptModel.tenant_id == tenant_id,
                PaymentAttemptModel.attempt_id == target_attempt.attempt_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(PaymentAttemptModel.from_domain(target_attempt))
            else:
                existing.intent_id = target_attempt.intent_id
                existing.amount_minor = target_attempt.amount.minor_units
                existing.amount_currency = target_attempt.amount.currency
                existing.status = target_attempt.status.value
                existing.idempotency_key = target_attempt.idempotency_key
                existing.provider = target_attempt.provider
                existing.provider_payment_id = target_attempt.provider_payment_id
                existing.failure_reason = target_attempt.failure_reason
                existing.updated_at = target_attempt.updated_at
            session.commit()


class SqlAlchemyRefundRepository(BaseSqlAlchemyRepository, RefundRepository):
    """SQLAlchemy-backed repository for Refund persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, refund_id: str) -> Refund | None:
        with self._session() as session:
            stmt = select(RefundModel).where(
                RefundModel.tenant_id == tenant_id,
                RefundModel.refund_id == refund_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            created_at = model.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return Refund(
                refund_id=model.refund_id,
                payment_attempt_id=model.payment_attempt_id,
                tenant_id=model.tenant_id,
                amount=Money(model.amount_minor, model.currency),
                reason=model.reason or "",
                created_at=created_at,
            )

    def save(self, tenant_id: str, refund: Refund) -> None:
        if refund.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Refund tenant '{refund.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            stmt = select(RefundModel).where(
                RefundModel.tenant_id == tenant_id,
                RefundModel.refund_id == refund.refund_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(
                    RefundModel(
                        refund_id=refund.refund_id,
                        tenant_id=refund.tenant_id,
                        payment_attempt_id=refund.payment_attempt_id,
                        amount_minor=refund.amount.minor_units,
                        currency=refund.amount.currency,
                        reason=refund.reason,
                        created_at=refund.created_at,
                    )
                )
            else:
                existing.payment_attempt_id = refund.payment_attempt_id
                existing.amount_minor = refund.amount.minor_units
                existing.currency = refund.amount.currency
                existing.reason = refund.reason
            session.commit()


class SqlAlchemyIdempotencyStore(BaseSqlAlchemyRepository, IdempotencyStore):
    """SQLAlchemy-backed repository for IdempotencyRecord persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, key: str) -> IdempotencyRecord | None:
        with self._session() as session:
            stmt = select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.tenant_id == tenant_id,
                IdempotencyRecordModel.key == key,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return IdempotencyRecord(
                key=model.key,
                tenant_id=model.tenant_id,
                operation_type=model.operation_type,
                request_hash=model.request_hash,
                result_data=json.loads(model.result_data_json),
                created_at=model.created_at,
            )

    def save(self, tenant_id: str, record: IdempotencyRecord) -> None:
        if record.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Idempotency record tenant '{record.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            stmt = select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.tenant_id == tenant_id,
                IdempotencyRecordModel.key == record.key,
            )
            existing = session.scalar(stmt)
            id_key = f"{tenant_id}:{record.key}"
            if existing is None:
                session.add(
                    IdempotencyRecordModel(
                        idempotency_id=id_key,
                        tenant_id=record.tenant_id,
                        key=record.key,
                        operation_type=record.operation_type,
                        request_hash=record.request_hash,
                        result_data_json=json.dumps(record.result_data),
                        created_at=record.created_at,
                    )
                )
            else:
                existing.operation_type = record.operation_type
                existing.request_hash = record.request_hash
                existing.result_data_json = json.dumps(record.result_data)
            session.commit()


__all__ = [
    "SqlAlchemyIdempotencyStore",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyPaymentRepository",
]

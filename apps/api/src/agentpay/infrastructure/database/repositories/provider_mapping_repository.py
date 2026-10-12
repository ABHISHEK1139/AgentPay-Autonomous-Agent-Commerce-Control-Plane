"""SQLAlchemy implementation of ProviderTransactionMappingRepository port (§39, §41, §49)."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.payment_port import ProviderTransactionMappingRepository
from agentpay.domain.payments import ProviderTransactionMapping
from agentpay.infrastructure.database.models.provider_mapping import (
    ProviderTransactionMappingModel,
)
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyProviderTransactionMappingRepository(
    BaseSqlAlchemyRepository, ProviderTransactionMappingRepository
):
    """SQLAlchemy-backed repository for ProviderTransactionMapping persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def save(self, mapping: ProviderTransactionMapping) -> None:
        with self._session() as session:
            stmt = select(ProviderTransactionMappingModel).where(
                ProviderTransactionMappingModel.mapping_id == mapping.mapping_id
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(ProviderTransactionMappingModel.from_domain(mapping))
            else:
                existing.tenant_id = mapping.tenant_id
                existing.attempt_id = mapping.attempt_id
                existing.provider = mapping.provider
                existing.provider_payment_id = mapping.provider_payment_id
                existing.provider_order_id = mapping.provider_order_id
                existing.provider_account_id = mapping.provider_account_id
                existing.order_id = mapping.order_id
            session.commit()

    def find_by_provider_transaction(
        self,
        provider: str,
        provider_transaction_id: str,
        provider_account_id: str | None = None,
    ) -> ProviderTransactionMapping | None:
        with self._session() as session:
            conditions = [
                or_(
                    ProviderTransactionMappingModel.provider_payment_id == provider_transaction_id,
                    ProviderTransactionMappingModel.provider_order_id == provider_transaction_id,
                ),
            ]
            if provider:
                conditions.append(ProviderTransactionMappingModel.provider == provider)
            if provider_account_id is not None:
                conditions.append(
                    or_(
                        ProviderTransactionMappingModel.provider_account_id == provider_account_id,
                        ProviderTransactionMappingModel.provider_account_id.is_(None),
                        ProviderTransactionMappingModel.provider_account_id == "",
                    )
                )

            stmt = select(ProviderTransactionMappingModel).where(*conditions)
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def find_by_attempt_id(
        self,
        tenant_id: str,
        attempt_id: str,
    ) -> ProviderTransactionMapping | None:
        with self._session() as session:
            stmt = select(ProviderTransactionMappingModel).where(
                ProviderTransactionMappingModel.tenant_id == tenant_id,
                ProviderTransactionMappingModel.attempt_id == attempt_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

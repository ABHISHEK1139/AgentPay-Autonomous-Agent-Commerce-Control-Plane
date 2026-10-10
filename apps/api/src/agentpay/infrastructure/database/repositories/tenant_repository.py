"""SQLAlchemy implementation of TenantRepository port (§49, §50)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.identity_port import TenantRepository
from agentpay.domain.identity import Tenant
from agentpay.infrastructure.database.models.tenant import TenantModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyTenantRepository(BaseSqlAlchemyRepository, TenantRepository):
    """SQLAlchemy-backed repository for Tenant persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str) -> Tenant | None:
        with self._session() as session:
            stmt = select(TenantModel).where(TenantModel.tenant_id == tenant_id)
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant: Tenant) -> None:
        with self._session() as session:
            model = session.scalar(
                select(TenantModel).where(TenantModel.tenant_id == tenant.tenant_id)
            )
            if model is None:
                session.add(TenantModel.from_domain(tenant))
            else:
                model.name = tenant.name
                model.status = tenant.status.value
            session.commit()

    def exists(self, tenant_id: str) -> bool:
        with self._session() as session:
            model = session.scalar(select(TenantModel).where(TenantModel.tenant_id == tenant_id))
            return model is not None

    def list_tenants(self) -> list[Tenant]:
        with self._session() as session:
            stmt = select(TenantModel).order_by(TenantModel.tenant_id)
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def list(self) -> list[Tenant]:
        return self.list_tenants()

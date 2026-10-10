"""Re-export and implementation of identity repositories (§15, §16, §49)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.identity_port import (
    AgentPermissionsRepository,
)
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.identity import AgentPermissions
from agentpay.infrastructure.database.models.identity import (
    AgentPermissionsModel,
)
from agentpay.infrastructure.database.repositories.agent_repository import (
    SqlAlchemyAgentRepository,
)
from agentpay.infrastructure.database.repositories.base import (
    BaseSqlAlchemyRepository,
)


class SqlAlchemyAgentPermissionsRepository(BaseSqlAlchemyRepository, AgentPermissionsRepository):
    """SQLAlchemy implementation of AgentPermissionsRepository port (§16, §49)."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, agent_id: str) -> AgentPermissions | None:
        with self._session() as session:
            stmt = select(AgentPermissionsModel).where(
                AgentPermissionsModel.tenant_id == tenant_id,
                AgentPermissionsModel.agent_id == agent_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(
        self,
        tenant_id_or_permissions: str | AgentPermissions,
        permissions: AgentPermissions | None = None,
    ) -> None:
        if isinstance(tenant_id_or_permissions, AgentPermissions):
            target = tenant_id_or_permissions
            tenant_id = target.tenant_id
        else:
            tenant_id = tenant_id_or_permissions
            if permissions is None:
                raise ValueError("Permissions entity must be provided")
            target = permissions

        if target.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Permissions tenant '{target.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(AgentPermissionsModel).where(
                AgentPermissionsModel.tenant_id == tenant_id,
                AgentPermissionsModel.agent_id == target.agent_id,
            )
            model = session.scalar(stmt)
            if model is None:
                session.add(AgentPermissionsModel.from_domain(target))
            else:
                model.capabilities = list(target.capabilities)
                model.per_transaction_minor = target.limits.per_transaction_minor
                model.daily_minor = target.limits.daily_minor
                model.limits_approval_minor = target.limits.requires_approval_above_minor
                model.allowed_categories = list(target.allowed_categories)
                model.requires_approval_above_minor = target.requires_approval_above_minor
                model.version = target.version
            session.commit()


__all__ = ["SqlAlchemyAgentPermissionsRepository", "SqlAlchemyAgentRepository"]

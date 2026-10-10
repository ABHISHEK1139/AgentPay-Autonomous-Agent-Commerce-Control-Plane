"""SQLAlchemy implementation of AgentRepository and AgentPermissionsRepository ports (§15, §16, §49, §50)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.identity_port import (
    AgentPermissionsRepository,
    AgentRepository,
)
from agentpay.domain.errors import (
    AgentRevokedError,
    CrossTenantAccessError,
    IdentityError,
)
from agentpay.domain.identity import Agent, AgentPermissions, AgentStatus
from agentpay.infrastructure.database.models.agent import AgentModel
from agentpay.infrastructure.database.models.identity import AgentPermissionsModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyAgentRepository(BaseSqlAlchemyRepository, AgentRepository):
    """SQLAlchemy-backed repository for Agent persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, agent_id: str) -> Agent | None:
        with self._session() as session:
            stmt = select(AgentModel).where(
                AgentModel.tenant_id == tenant_id,
                AgentModel.agent_id == agent_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id_or_agent: str | Agent, agent: Agent | None = None) -> None:
        """Persist or update an agent within a tenant partition.

        Supports both save(agent) and save(tenant_id, agent).
        """
        if isinstance(tenant_id_or_agent, Agent):
            target_agent = tenant_id_or_agent
            tenant_id = target_agent.tenant_id
        else:
            tenant_id = tenant_id_or_agent
            if agent is None:
                raise ValueError("Agent entity must be provided")
            target_agent = agent

        if target_agent.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Agent tenant '{target_agent.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(AgentModel).where(
                AgentModel.tenant_id == tenant_id,
                AgentModel.agent_id == target_agent.agent_id,
            )
            existing = session.scalar(stmt)
            if existing is not None and existing.status == AgentStatus.REVOKED.value:
                raise AgentRevokedError(
                    f"Cannot overwrite terminal REVOKED agent '{target_agent.agent_id}'"
                )

            if existing is None:
                session.add(AgentModel.from_domain(target_agent))
            else:
                existing.principal_id = target_agent.principal_id
                existing.issuer = target_agent.issuer
                existing.capabilities = list(target_agent.capabilities)
                existing.scopes = list(target_agent.scopes)
                existing.status = target_agent.status.value
                existing.expires_at = target_agent.expires_at
            session.commit()

    def list(self, tenant_id: str) -> list[Agent]:
        with self._session() as session:
            stmt = (
                select(AgentModel)
                .where(AgentModel.tenant_id == tenant_id)
                .order_by(AgentModel.agent_id)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def update_status(self, tenant_id: str, agent_id: str, status: AgentStatus) -> None:
        with self._session() as session:
            stmt = select(AgentModel).where(
                AgentModel.tenant_id == tenant_id,
                AgentModel.agent_id == agent_id,
            )
            model = session.scalar(stmt)
            if model is None:
                raise IdentityError(f"Agent '{agent_id}' not found in tenant '{tenant_id}'")
            if model.status == AgentStatus.REVOKED.value:
                raise AgentRevokedError(f"Agent '{agent_id}' is already permanently REVOKED")

            model.status = status.value
            session.commit()


class SqlAlchemyAgentPermissionsRepository(BaseSqlAlchemyRepository, AgentPermissionsRepository):
    """SQLAlchemy-backed repository for AgentPermissions persistence."""

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

    def save(self, tenant_id: str, permissions: AgentPermissions) -> None:
        if permissions.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Permissions tenant '{permissions.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            stmt = select(AgentPermissionsModel).where(
                AgentPermissionsModel.tenant_id == tenant_id,
                AgentPermissionsModel.agent_id == permissions.agent_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(AgentPermissionsModel.from_domain(permissions))
            else:
                existing.capabilities = list(permissions.capabilities)
                existing.per_transaction_minor = permissions.limits.per_transaction_minor
                existing.daily_minor = permissions.limits.daily_minor
                existing.limits_approval_minor = permissions.limits.requires_approval_above_minor
                existing.allowed_categories = list(permissions.allowed_categories)
                existing.requires_approval_above_minor = permissions.requires_approval_above_minor
                existing.version = permissions.version
            session.commit()


__all__ = [
    "SqlAlchemyAgentPermissionsRepository",
    "SqlAlchemyAgentRepository",
]

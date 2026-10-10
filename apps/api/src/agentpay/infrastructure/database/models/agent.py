"""Relational model for Agent entity (§15, §49)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.identity import Agent, AgentStatus
from agentpay.infrastructure.database.base import Base


class AgentModel(Base):
    """SQLAlchemy model for Agent identity entity (§15, §49)."""

    __tablename__ = "agents"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (Index("ix_agents_tenant_status", "tenant_id", "status"),)

    def to_domain(self) -> Agent:
        """Convert relational model instance to pure domain Agent dataclass."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        raw_caps: Any = self.capabilities
        caps_tuple = tuple(raw_caps) if isinstance(raw_caps, (list, tuple)) else ()

        raw_scopes: Any = self.scopes
        scopes_tuple = tuple(raw_scopes) if isinstance(raw_scopes, (list, tuple)) else ()

        return Agent(
            agent_id=self.agent_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            issuer=self.issuer,
            capabilities=caps_tuple,
            scopes=scopes_tuple,
            created_at=created_at,
            expires_at=expires_at,
            status=AgentStatus(self.status),
        )

    @classmethod
    def from_domain(cls, agent: Agent) -> AgentModel:
        """Create relational model instance from pure domain Agent dataclass."""
        return cls(
            tenant_id=agent.tenant_id,
            agent_id=agent.agent_id,
            principal_id=agent.principal_id,
            issuer=agent.issuer,
            capabilities=list(agent.capabilities),
            scopes=list(agent.scopes),
            status=agent.status.value,
            created_at=agent.created_at,
            expires_at=agent.expires_at,
        )

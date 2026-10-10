"""Relational models for Agent and AgentPermissions entities (§15, §16, §49)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.identity import AgentPermissions
from agentpay.domain.policy import SpendingLimits
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.models.agent import AgentModel


class AgentPermissionsModel(Base):
    """SQLAlchemy model for AgentPermissions entity (§16)."""

    __tablename__ = "agent_permissions"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    per_transaction_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    daily_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    limits_approval_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    allowed_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    requires_approval_above_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def to_domain(self) -> AgentPermissions:
        """Convert relational model instance to pure domain AgentPermissions dataclass."""
        raw_caps: Any = self.capabilities
        caps_tuple = tuple(raw_caps) if isinstance(raw_caps, (list, tuple)) else ()

        raw_cats: Any = self.allowed_categories
        cats_tuple = tuple(raw_cats) if isinstance(raw_cats, (list, tuple)) else ()

        return AgentPermissions(
            agent_id=self.agent_id,
            tenant_id=self.tenant_id,
            capabilities=caps_tuple,
            limits=SpendingLimits(
                per_transaction_minor=self.per_transaction_minor,
                daily_minor=self.daily_minor,
                requires_approval_above_minor=self.limits_approval_minor,
            ),
            allowed_categories=cats_tuple,
            requires_approval_above_minor=self.requires_approval_above_minor,
            version=self.version,
        )

    @classmethod
    def from_domain(cls, p: AgentPermissions) -> AgentPermissionsModel:
        """Create relational model instance from pure domain AgentPermissions dataclass."""
        return cls(
            tenant_id=p.tenant_id,
            agent_id=p.agent_id,
            capabilities=list(p.capabilities),
            per_transaction_minor=p.limits.per_transaction_minor,
            daily_minor=p.limits.daily_minor,
            limits_approval_minor=p.limits.requires_approval_above_minor,
            allowed_categories=list(p.allowed_categories),
            requires_approval_above_minor=p.requires_approval_above_minor,
            version=p.version,
        )


__all__ = ["AgentModel", "AgentPermissionsModel"]

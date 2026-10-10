"""Relational models for Tenant and User entities (§49, §50)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentpay.domain.identity import Tenant, TenantStatus
from agentpay.infrastructure.database.base import Base


class TenantModel(Base):
    """SQLAlchemy model for Tenant entity (§49)."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    users: Mapped[list[UserModel]] = relationship("UserModel", back_populates="tenant")

    def to_domain(self) -> Tenant:
        """Convert relational model instance to pure domain Tenant dataclass."""
        return Tenant(
            tenant_id=self.tenant_id,
            name=self.name,
            status=TenantStatus(self.status),
        )

    @classmethod
    def from_domain(cls, tenant: Tenant) -> TenantModel:
        """Create relational model instance from pure domain Tenant dataclass."""
        return cls(
            tenant_id=tenant.tenant_id,
            name=tenant.name,
            status=tenant.status.value,
        )


class UserModel(Base):
    """SQLAlchemy model for User principal entity."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    tenant: Mapped[TenantModel] = relationship("TenantModel", back_populates="users")

    __table_args__ = (Index("ix_users_tenant_email", "tenant_id", "email", unique=True),)

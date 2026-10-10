"""Relational model for Credential entity (§14, §49)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.identity import (
    Credential,
    CredentialStatus,
    CredentialType,
    UserRole,
)
from agentpay.infrastructure.database.base import Base


class CredentialModel(Base):
    """SQLAlchemy model for API and Bearer credentials (§14)."""

    __tablename__ = "credentials"

    credential_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_credentials_tenant_principal", "tenant_id", "principal_id"),)

    def to_domain(self) -> Credential:
        """Convert relational model instance to pure domain Credential dataclass."""
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        raw_scopes: Any = self.scopes
        scopes_tuple = tuple(raw_scopes) if isinstance(raw_scopes, (list, tuple)) else ()

        return Credential(
            credential_id=self.credential_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            credential_type=CredentialType(self.credential_type),
            secret_hash=self.secret_hash,
            expires_at=expires_at,
            status=CredentialStatus(self.status),
            role=UserRole(self.role) if self.role else None,
            scopes=scopes_tuple,
        )

    @classmethod
    def from_domain(cls, cred: Credential) -> CredentialModel:
        """Create relational model instance from pure domain Credential dataclass."""
        return cls(
            credential_id=cred.credential_id,
            tenant_id=cred.tenant_id,
            principal_id=cred.principal_id,
            credential_type=cred.credential_type.value,
            secret_hash=cred.secret_hash,
            status=cred.status.value,
            role=cred.role.value if cred.role else None,
            scopes=list(cred.scopes),
            expires_at=cred.expires_at,
        )

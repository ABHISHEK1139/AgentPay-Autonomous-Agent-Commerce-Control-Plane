"""Relational Database Foundation and Declarative Base (§49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy 2.0 models."""

    pass


class TenantScopedMixin:
    """Mixin enforcing explicit tenant isolation (§49, §50)."""

    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)


class TimestampMixin:
    """Mixin providing created_at timestamp with timezone."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

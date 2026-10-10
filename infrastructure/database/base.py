"""Root database base re-export (§9, §49)."""

from __future__ import annotations

from agentpay.infrastructure.database.base import Base, TenantScopedMixin, TimestampMixin

__all__ = ["Base", "TenantScopedMixin", "TimestampMixin"]

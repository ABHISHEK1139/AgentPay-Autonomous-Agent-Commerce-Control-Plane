"""SQLAlchemy model for persistent emergency pause records (§21, §67, AP-008)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.application.ports.emergency_pause_port import PauseRecord
from agentpay.infrastructure.database.base import Base


class EmergencyPauseRecordModel(Base):
    """Relational model for cluster-wide emergency kill-switch pause states."""

    __tablename__ = "emergency_pause_records"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    paused_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    resumed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> PauseRecord:
        """Convert relational model instance to pure domain PauseRecord."""
        p_at = self.paused_at
        if p_at.tzinfo is None:
            p_at = p_at.replace(tzinfo=UTC)

        r_at = self.resumed_at
        if r_at is not None and r_at.tzinfo is None:
            r_at = r_at.replace(tzinfo=UTC)

        return PauseRecord(
            scope=self.scope,
            is_paused=self.is_paused,
            paused_by=self.paused_by,
            reason=self.reason,
            paused_at=p_at,
            resumed_by=self.resumed_by,
            resumed_at=r_at,
        )

    @classmethod
    def from_domain(cls, record: PauseRecord) -> EmergencyPauseRecordModel:
        """Create relational model from pure domain entity."""
        return cls(
            scope=record.scope,
            is_paused=record.is_paused,
            paused_by=record.paused_by,
            reason=record.reason,
            paused_at=record.paused_at,
            resumed_by=record.resumed_by,
            resumed_at=record.resumed_at,
        )

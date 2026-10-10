"""Relational models for Payment, Order, Refund, and Idempotency entities (§39, §41, §42, §49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.models.order import OrderModel
from agentpay.infrastructure.database.models.payment import PaymentAttemptModel


class RefundModel(Base):
    """SQLAlchemy model for Refund transaction entity (§39)."""

    __tablename__ = "refunds"

    refund_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payment_attempt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("payment_attempts.attempt_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    payment_attempt: Mapped[PaymentAttemptModel | None] = relationship(
        "PaymentAttemptModel",
        foreign_keys=[payment_attempt_id],
        primaryjoin="RefundModel.payment_attempt_id == foreign(PaymentAttemptModel.attempt_id)",
    )


class IdempotencyRecordModel(Base):
    """SQLAlchemy model for IdempotencyRecord entity (§42)."""

    __tablename__ = "idempotency_records"

    idempotency_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    result_data_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("ix_idempotency_tenant_key", "tenant_id", "key", unique=True),)


__all__ = [
    "IdempotencyRecordModel",
    "OrderModel",
    "PaymentAttemptModel",
    "RefundModel",
]

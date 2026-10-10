"""Relational model for PaymentAttempt entity (§39, §41, §49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.money import Money
from agentpay.domain.payments import PaymentAttempt, PaymentStatus
from agentpay.infrastructure.database.base import Base


class PaymentAttemptModel(Base):
    """SQLAlchemy model for PaymentAttempt entity (§39, §41)."""

    __tablename__ = "payment_attempts"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), default="mock", nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_payment_attempts_tenant_idempotency"
        ),
        Index("ix_payment_attempts_tenant_status", "tenant_id", "status"),
    )

    def to_domain(self) -> PaymentAttempt:
        """Convert relational model instance to pure domain PaymentAttempt dataclass."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        updated_at = self.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        return PaymentAttempt(
            attempt_id=self.attempt_id,
            intent_id=self.intent_id,
            tenant_id=self.tenant_id,
            amount=Money(self.amount_minor, self.amount_currency),
            status=PaymentStatus(self.status),
            idempotency_key=self.idempotency_key,
            provider=self.provider,
            provider_payment_id=self.provider_payment_id,
            created_at=created_at,
            updated_at=updated_at,
            failure_reason=self.failure_reason,
        )

    @classmethod
    def from_domain(cls, attempt: PaymentAttempt) -> PaymentAttemptModel:
        """Create relational model instance from pure domain PaymentAttempt dataclass."""
        return cls(
            tenant_id=attempt.tenant_id,
            attempt_id=attempt.attempt_id,
            intent_id=attempt.intent_id,
            amount_minor=attempt.amount.minor_units,
            amount_currency=attempt.amount.currency,
            status=attempt.status.value,
            idempotency_key=attempt.idempotency_key,
            provider=attempt.provider,
            provider_payment_id=attempt.provider_payment_id,
            failure_reason=attempt.failure_reason,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
        )

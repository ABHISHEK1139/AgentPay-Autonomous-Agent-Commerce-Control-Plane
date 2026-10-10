"""Relational model for Order entity (§41, §44, §49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from agentpay.domain.money import Money
from agentpay.domain.payments import Order, OrderStatus
from agentpay.infrastructure.database.base import Base


class OrderModel(Base):
    """SQLAlchemy model for Order commitment entity (§41)."""

    __tablename__ = "orders"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_amount_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING_PAYMENT", nullable=False)
    payment_attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_orders_tenant_status", "tenant_id", "status"),
        Index("ix_orders_tenant_buyer", "tenant_id", "buyer_id"),
    )

    def to_domain(self) -> Order:
        """Convert relational model instance to pure domain Order dataclass."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return Order(
            order_id=self.order_id,
            tenant_id=self.tenant_id,
            merchant_id=self.merchant_id,
            buyer_id=self.buyer_id,
            agent_id=self.agent_id,
            variant_id=self.variant_id,
            quantity=self.quantity,
            total_amount=Money(self.total_amount_minor, self.total_amount_currency),
            status=OrderStatus(self.status),
            payment_attempt_id=self.payment_attempt_id,
            created_at=created_at,
        )

    @classmethod
    def from_domain(cls, order: Order) -> OrderModel:
        """Create relational model instance from pure domain Order dataclass."""
        return cls(
            tenant_id=order.tenant_id,
            order_id=order.order_id,
            merchant_id=order.merchant_id,
            buyer_id=order.buyer_id,
            agent_id=order.agent_id,
            variant_id=order.variant_id,
            quantity=order.quantity,
            total_amount_minor=order.total_amount.minor_units,
            total_amount_currency=order.total_amount.currency,
            status=order.status.value,
            payment_attempt_id=order.payment_attempt_id,
            created_at=order.created_at,
        )

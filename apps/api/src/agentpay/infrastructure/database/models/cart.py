"""Relational models for the Cart aggregate (§38, §49, ADR-0006)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentpay.domain.cart import Cart, CartItem, CartStatus
from agentpay.infrastructure.database.base import Base


class CartModel(Base):
    """SQLAlchemy model for Cart entity (ADR-0006)."""

    __tablename__ = "carts"

    cart_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
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

    items: Mapped[list[CartItemModel]] = relationship(
        "CartItemModel",
        back_populates="cart",
        cascade="all, delete-orphan",
        order_by="CartItemModel.position",
    )

    __table_args__ = (
        Index("ix_carts_tenant_status", "tenant_id", "status"),
        Index("ix_carts_tenant_principal", "tenant_id", "principal_id"),
    )

    def to_domain(self) -> Cart:
        """Convert relational model instance to pure domain Cart dataclass."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        updated_at = self.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        return Cart(
            cart_id=self.cart_id,
            tenant_id=self.tenant_id,
            principal_id=self.principal_id,
            agent_id=self.agent_id,
            merchant_id=self.merchant_id,
            status=CartStatus(self.status),
            items=tuple(
                CartItem(variant_id=item.variant_id, quantity=item.quantity) for item in self.items
            ),
            created_at=created_at,
            updated_at=updated_at,
        )

    @classmethod
    def from_domain(cls, cart: Cart) -> CartModel:
        """Create relational model instance from pure domain Cart dataclass."""
        model = cls(
            cart_id=cart.cart_id,
            tenant_id=cart.tenant_id,
            principal_id=cart.principal_id,
            agent_id=cart.agent_id,
            merchant_id=cart.merchant_id,
            status=cart.status.value,
            created_at=cart.created_at,
            updated_at=cart.updated_at,
        )
        model.items = [
            CartItemModel(
                cart_id=cart.cart_id,
                position=position,
                variant_id=item.variant_id,
                quantity=item.quantity,
            )
            for position, item in enumerate(cart.items)
        ]
        return model


class CartItemModel(Base):
    """SQLAlchemy model for a single cart line item (ADR-0006)."""

    __tablename__ = "cart_items"

    cart_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("carts.cart_id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    cart: Mapped[CartModel] = relationship("CartModel", back_populates="items")

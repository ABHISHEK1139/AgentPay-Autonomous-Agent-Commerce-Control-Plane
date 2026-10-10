"""Relational models for Commerce catalog, Inventory, and Evidence entities (§30, §32, §37, §49)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentpay.domain.commerce import Product, ProductVariant
from agentpay.domain.money import Money
from agentpay.infrastructure.database.base import Base


class MerchantModel(Base):
    """SQLAlchemy model for Merchant entity (§30)."""

    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="REGISTERED", nullable=False)
    allowed_agent_purchase: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_order_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    products: Mapped[list[ProductModel]] = relationship(
        "ProductModel",
        back_populates="merchant",
        cascade="all, delete-orphan",
    )


class ProductModel(Base):
    """SQLAlchemy model for Product entity (§30)."""

    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("merchants.merchant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    merchant: Mapped[MerchantModel] = relationship("MerchantModel", back_populates="products")
    variants: Mapped[list[ProductVariantModel]] = relationship(
        "ProductVariantModel", back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_products_tenant_search", "tenant_id", "category", "name"),)

    def to_domain(self) -> Product:
        """Convert relational model instance to pure domain Product dataclass."""
        return Product(
            product_id=self.product_id,
            tenant_id=self.tenant_id,
            merchant_id=self.merchant_id,
            name=self.name,
            category=self.category,
            description=self.description,
        )

    @classmethod
    def from_domain(cls, product: Product) -> ProductModel:
        """Create relational model instance from pure domain Product dataclass."""
        return cls(
            tenant_id=product.tenant_id,
            product_id=product.product_id,
            merchant_id=product.merchant_id,
            name=product.name,
            category=product.category,
            description=product.description,
        )


class ProductVariantModel(Base):
    """SQLAlchemy model for ProductVariant entity (§30)."""

    __tablename__ = "product_variants"

    variant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.product_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    product: Mapped[ProductModel] = relationship("ProductModel", back_populates="variants")
    inventory: Mapped[InventoryModel | None] = relationship(
        "InventoryModel", back_populates="variant", uselist=False, cascade="all, delete-orphan"
    )

    def to_domain(self) -> ProductVariant:
        """Convert relational model instance to pure domain ProductVariant dataclass."""
        return ProductVariant(
            variant_id=self.variant_id,
            product_id=self.product_id,
            sku=self.sku,
            name=self.name,
            price=Money(self.price_minor, self.price_currency),
        )

    @classmethod
    def from_domain(cls, variant: ProductVariant, tenant_id: str) -> ProductVariantModel:
        """Create relational model instance from pure domain ProductVariant dataclass."""
        return cls(
            tenant_id=tenant_id,
            variant_id=variant.variant_id,
            product_id=variant.product_id,
            sku=variant.sku,
            name=variant.name,
            price_minor=variant.price.minor_units,
            price_currency=variant.price.currency,
        )


class InventoryModel(Base):
    """SQLAlchemy model for Inventory stock entity (§30, §49)."""

    __tablename__ = "inventory"

    variant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("product_variants.variant_id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    variant: Mapped[ProductVariantModel | None] = relationship(
        "ProductVariantModel", back_populates="inventory"
    )


class EvidenceModel(Base):
    """SQLAlchemy model for Evidence entity (§32)."""

    __tablename__ = "product_evidence"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (Index("ix_evidence_tenant_resource", "tenant_id", "resource_id"),)

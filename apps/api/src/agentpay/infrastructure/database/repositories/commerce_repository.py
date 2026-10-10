"""SQLAlchemy implementation of Commerce repositories (§30, §32, §37, §49, §50)."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.commerce_port import (
    EvidenceRepository,
    InventoryRepository,
    MerchantRepository,
    ProductRepository,
    VariantRepository,
)
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.errors import (
    CrossTenantAccessError,
    InsufficientInventoryError,
)
from agentpay.domain.evidence import (
    Evidence,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceType,
)
from agentpay.infrastructure.database.models.commerce import (
    EvidenceModel,
    InventoryModel,
    MerchantModel,
    ProductModel,
    ProductVariantModel,
)
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyProductRepository(BaseSqlAlchemyRepository, ProductRepository):
    """SQLAlchemy-backed repository for Product and ProductVariant persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, product_id: str) -> Product | None:
        with self._session() as session:
            stmt = select(ProductModel).where(
                ProductModel.tenant_id == tenant_id,
                ProductModel.product_id == product_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def get_variant(self, tenant_id: str, variant_id: str) -> ProductVariant | None:
        with self._session() as session:
            stmt = select(ProductVariantModel).where(
                ProductVariantModel.tenant_id == tenant_id,
                ProductVariantModel.variant_id == variant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id_or_product: str | Product, product: Product | None = None) -> None:
        """Persist or update a product entity.

        Supports both save(product) and save(tenant_id, product).
        """
        if isinstance(tenant_id_or_product, Product):
            target_product = tenant_id_or_product
            tenant_id = target_product.tenant_id
        else:
            tenant_id = tenant_id_or_product
            if product is None:
                raise ValueError("Product entity must be provided")
            target_product = product

        if target_product.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Product tenant '{target_product.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(ProductModel).where(
                ProductModel.tenant_id == tenant_id,
                ProductModel.product_id == target_product.product_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(ProductModel.from_domain(target_product))
            else:
                existing.merchant_id = target_product.merchant_id
                existing.name = target_product.name
                existing.category = target_product.category
                existing.description = target_product.description
            session.commit()

    def save_variant(
        self,
        variant_or_tenant: str | ProductVariant,
        variant: ProductVariant | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Persist or update a product variant entity.

        Supports save_variant(variant), save_variant(variant, tenant_id),
        and save_variant(tenant_id, variant).
        """
        target_variant: ProductVariant
        resolved_tenant_id: str | None

        if isinstance(variant_or_tenant, ProductVariant):
            target_variant = variant_or_tenant
            resolved_tenant_id = tenant_id
        else:
            resolved_tenant_id = variant_or_tenant
            if variant is None:
                raise ValueError("ProductVariant entity must be provided")
            target_variant = variant

        with self._session() as session:
            if resolved_tenant_id is None:
                parent = session.scalar(
                    select(ProductModel).where(ProductModel.product_id == target_variant.product_id)
                )
                if parent is not None:
                    resolved_tenant_id = parent.tenant_id
                else:
                    raise ValueError(
                        f"Cannot resolve tenant_id for variant '{target_variant.variant_id}'"
                    )

            stmt = select(ProductVariantModel).where(
                ProductVariantModel.tenant_id == resolved_tenant_id,
                ProductVariantModel.variant_id == target_variant.variant_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(ProductVariantModel.from_domain(target_variant, resolved_tenant_id))
            else:
                existing.product_id = target_variant.product_id
                existing.sku = target_variant.sku
                existing.name = target_variant.name
                existing.price_minor = target_variant.price.minor_units
                existing.price_currency = target_variant.price.currency
            session.commit()

    def list_by_merchant(self, tenant_id: str, merchant_id: str) -> list[Product]:
        with self._session() as session:
            stmt = (
                select(ProductModel)
                .where(
                    ProductModel.tenant_id == tenant_id,
                    ProductModel.merchant_id == merchant_id,
                )
                .order_by(ProductModel.name)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def search(self, tenant_id: str, query: str) -> list[Product]:
        with self._session() as session:
            term = f"%{query.strip().lower()}%"
            stmt = (
                select(ProductModel)
                .where(
                    ProductModel.tenant_id == tenant_id,
                    or_(
                        ProductModel.name.ilike(term),
                        ProductModel.category.ilike(term),
                        ProductModel.description.ilike(term),
                    ),
                )
                .order_by(ProductModel.name)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]


class SqlAlchemyVariantRepository(BaseSqlAlchemyRepository, VariantRepository):
    """SQLAlchemy-backed repository for Variant lookup and persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, variant_id: str) -> ProductVariant | None:
        with self._session() as session:
            stmt = select(ProductVariantModel).where(
                ProductVariantModel.tenant_id == tenant_id,
                ProductVariantModel.variant_id == variant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id: str, variant: ProductVariant) -> None:
        with self._session() as session:
            stmt = select(ProductVariantModel).where(
                ProductVariantModel.tenant_id == tenant_id,
                ProductVariantModel.variant_id == variant.variant_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(ProductVariantModel.from_domain(variant, tenant_id))
            else:
                existing.product_id = variant.product_id
                existing.sku = variant.sku
                existing.name = variant.name
                existing.price_minor = variant.price.minor_units
                existing.price_currency = variant.price.currency
            session.commit()

    def list_by_product(self, tenant_id: str, product_id: str) -> list[ProductVariant]:
        with self._session() as session:
            stmt = (
                select(ProductVariantModel)
                .where(
                    ProductVariantModel.tenant_id == tenant_id,
                    ProductVariantModel.product_id == product_id,
                )
                .order_by(ProductVariantModel.variant_id)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]


class SqlAlchemyInventoryRepository(BaseSqlAlchemyRepository, InventoryRepository):
    """SQLAlchemy-backed repository for Inventory stock management."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, variant_id: str) -> Inventory | None:
        with self._session() as session:
            stmt = select(InventoryModel).where(
                InventoryModel.tenant_id == tenant_id,
                InventoryModel.variant_id == variant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return Inventory(
                variant_id=model.variant_id,
                tenant_id=model.tenant_id,
                available=model.available,
                reserved=model.reserved,
            )

    def save(self, tenant_id: str, inventory: Inventory) -> None:
        if inventory.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Inventory tenant '{inventory.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            stmt = select(InventoryModel).where(
                InventoryModel.tenant_id == tenant_id,
                InventoryModel.variant_id == inventory.variant_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(
                    InventoryModel(
                        variant_id=inventory.variant_id,
                        tenant_id=inventory.tenant_id,
                        available=inventory.available,
                        reserved=inventory.reserved,
                    )
                )
            else:
                existing.available = inventory.available
                existing.reserved = inventory.reserved
            session.commit()

    def reserve(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        with self._session() as session:
            stmt = select(InventoryModel).where(
                InventoryModel.tenant_id == tenant_id,
                InventoryModel.variant_id == variant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                raise InsufficientInventoryError(
                    f"No inventory record for variant '{variant_id}' in tenant '{tenant_id}'"
                )
            effective = model.available - model.reserved
            if effective < qty:
                raise InsufficientInventoryError(
                    f"Insufficient inventory for variant '{variant_id}': needed {qty}, available {effective}"
                )
            model.reserved += qty
            session.commit()
            return Inventory(
                variant_id=model.variant_id,
                tenant_id=model.tenant_id,
                available=model.available,
                reserved=model.reserved,
            )

    def release(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        with self._session() as session:
            stmt = select(InventoryModel).where(
                InventoryModel.tenant_id == tenant_id,
                InventoryModel.variant_id == variant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                raise InsufficientInventoryError(
                    f"No inventory record for variant '{variant_id}' in tenant '{tenant_id}'"
                )
            model.reserved = max(0, model.reserved - qty)
            session.commit()
            return Inventory(
                variant_id=model.variant_id,
                tenant_id=model.tenant_id,
                available=model.available,
                reserved=model.reserved,
            )


class SqlAlchemyMerchantRepository(BaseSqlAlchemyRepository, MerchantRepository):
    """SQLAlchemy-backed repository for Merchant entity persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, merchant_id: str) -> Merchant | None:
        with self._session() as session:
            stmt = select(MerchantModel).where(
                MerchantModel.tenant_id == tenant_id,
                MerchantModel.merchant_id == merchant_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return Merchant(
                merchant_id=model.merchant_id,
                tenant_id=model.tenant_id,
                name=model.name,
                status=MerchantStatus(model.status),
                allowed_agent_purchase=model.allowed_agent_purchase,
                max_order_amount_minor=model.max_order_amount_minor,
            )

    def save(self, tenant_id: str, merchant: Merchant) -> None:
        if merchant.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Merchant tenant '{merchant.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            stmt = select(MerchantModel).where(
                MerchantModel.tenant_id == tenant_id,
                MerchantModel.merchant_id == merchant.merchant_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(
                    MerchantModel(
                        merchant_id=merchant.merchant_id,
                        tenant_id=merchant.tenant_id,
                        name=merchant.name,
                        status=merchant.status.value,
                        allowed_agent_purchase=merchant.allowed_agent_purchase,
                        max_order_amount_minor=merchant.max_order_amount_minor,
                    )
                )
            else:
                existing.name = merchant.name
                existing.status = merchant.status.value
                existing.allowed_agent_purchase = merchant.allowed_agent_purchase
                existing.max_order_amount_minor = merchant.max_order_amount_minor
            session.commit()


class SqlAlchemyEvidenceRepository(BaseSqlAlchemyRepository, EvidenceRepository):
    """SQLAlchemy-backed repository for Evidence persistence."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, evidence_id: str) -> Evidence | None:
        with self._session() as session:
            stmt = select(EvidenceModel).where(
                EvidenceModel.tenant_id == tenant_id,
                EvidenceModel.evidence_id == evidence_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            observed_at = model.observed_at
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            return Evidence(
                evidence_id=model.evidence_id,
                tenant_id=model.tenant_id,
                resource_id=model.resource_id,
                field_name=model.field_name,
                value=model.value,
                evidence_type=EvidenceType(model.evidence_type),
                source=EvidenceSource(
                    type=EvidenceSourceType(model.source_type),
                    id=model.source_id,
                    name=model.source_name,
                ),
                observed_at=observed_at,
            )

    def save(self, tenant_id: str, evidence: Evidence) -> None:
        if evidence.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Evidence tenant '{evidence.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._session() as session:
            session.add(
                EvidenceModel(
                    evidence_id=evidence.evidence_id,
                    tenant_id=evidence.tenant_id,
                    resource_id=evidence.resource_id,
                    field_name=evidence.field_name,
                    value=str(evidence.value),
                    evidence_type=evidence.evidence_type.value,
                    source_type=evidence.source.type.value,
                    source_id=evidence.source.id,
                    source_name=evidence.source.name,
                    observed_at=evidence.observed_at,
                )
            )
            session.commit()

    def list_for_resource(self, tenant_id: str, resource_id: str) -> list[Evidence]:
        with self._session() as session:
            stmt = (
                select(EvidenceModel)
                .where(
                    EvidenceModel.tenant_id == tenant_id,
                    EvidenceModel.resource_id == resource_id,
                )
                .order_by(EvidenceModel.observed_at)
            )
            models = session.scalars(stmt).all()
            return [
                Evidence(
                    evidence_id=m.evidence_id,
                    tenant_id=m.tenant_id,
                    resource_id=m.resource_id,
                    field_name=m.field_name,
                    value=m.value,
                    evidence_type=EvidenceType(m.evidence_type),
                    source=EvidenceSource(
                        type=EvidenceSourceType(m.source_type),
                        id=m.source_id,
                        name=m.source_name,
                    ),
                    observed_at=m.observed_at
                    if m.observed_at.tzinfo is not None
                    else m.observed_at.replace(tzinfo=UTC),
                )
                for m in models
            ]

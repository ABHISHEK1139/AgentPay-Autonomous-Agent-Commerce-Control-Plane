"""Application ports for Commerce: Products, Variants, Inventory, Merchants, and Evidence (§30, §32, §37).

Following Clean Architecture (§8, §10), every method strictly requires
an explicit `tenant_id` partition parameter (§49, §50).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    Product,
    ProductVariant,
)
from agentpay.domain.evidence import Evidence


@runtime_checkable
class ProductRepository(Protocol):
    """Port for product catalog lookup and persistence."""

    def get(self, tenant_id: str, product_id: str) -> Product | None:
        """Retrieve a product within a tenant partition."""
        ...

    def save(self, tenant_id: str, product: Product) -> None:
        """Persist or update a product within a tenant partition."""
        ...

    def list_by_merchant(self, tenant_id: str, merchant_id: str) -> list[Product]:
        """List products for a merchant within a tenant partition."""
        ...

    def search(self, tenant_id: str, query: str) -> list[Product]:
        """Search products by name or category within a tenant partition."""
        ...


@runtime_checkable
class VariantRepository(Protocol):
    """Port for product variant lookup and persistence."""

    def get(self, tenant_id: str, variant_id: str) -> ProductVariant | None:
        """Retrieve a variant within a tenant partition."""
        ...

    def save(self, tenant_id: str, variant: ProductVariant) -> None:
        """Persist or update a variant within a tenant partition."""
        ...

    def list_by_product(self, tenant_id: str, product_id: str) -> list[ProductVariant]:
        """List all variants for a product within a tenant partition."""
        ...


@runtime_checkable
class InventoryRepository(Protocol):
    """Port for inventory tracking and atomic reservation/release."""

    def get(self, tenant_id: str, variant_id: str) -> Inventory | None:
        """Retrieve inventory for a variant within a tenant partition."""
        ...

    def save(self, tenant_id: str, inventory: Inventory) -> None:
        """Persist or update inventory state within a tenant partition."""
        ...

    def reserve(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        """Reserve quantity for a variant, failing closed if insufficient stock."""
        ...

    def release(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        """Release previously reserved quantity for a variant."""
        ...


@runtime_checkable
class MerchantRepository(Protocol):
    """Port for merchant entity persistence and lookup."""

    def get(self, tenant_id: str, merchant_id: str) -> Merchant | None:
        """Retrieve a merchant within a tenant partition."""
        ...

    def save(self, tenant_id: str, merchant: Merchant) -> None:
        """Persist or update a merchant within a tenant partition."""
        ...


@runtime_checkable
class EvidenceRepository(Protocol):
    """Port for evidence provenance persistence and lookup (§32)."""

    def get(self, tenant_id: str, evidence_id: str) -> Evidence | None:
        """Retrieve an evidence record by ID within a tenant partition."""
        ...

    def save(self, tenant_id: str, evidence: Evidence) -> None:
        """Persist an evidence record within a tenant partition."""
        ...

    def list_for_resource(self, tenant_id: str, resource_id: str) -> list[Evidence]:
        """List all evidence recorded for a resource within a tenant partition."""
        ...

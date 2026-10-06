"""Thread-safe, tenant-partitioned in-memory commerce repositories (§30, §32, §49, §50).

Implements repositories for Products, Variants, Inventory, Merchants, and Evidence.
All methods strictly enforce the tenant boundary.
"""

from __future__ import annotations

import threading

from agentpay.adapters.in_memory.identity_repository import format_tenant_cache_key
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
    Product,
    ProductVariant,
)
from agentpay.domain.errors import (
    CrossTenantAccessError,
    InsufficientInventoryError,
    InvalidCommerceError,
)
from agentpay.domain.evidence import Evidence


class InMemoryProductRepository(ProductRepository):
    """Thread-safe in-memory store for Products."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # storage key: format_tenant_cache_key(tenant_id, "product", product_id)
        self._products: dict[str, Product] = {}

    def get(self, tenant_id: str, product_id: str) -> Product | None:
        key = format_tenant_cache_key(tenant_id, "product", product_id)
        with self._lock:
            return self._products.get(key)

    def save(self, tenant_id: str, product: Product) -> None:
        if product.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Product tenant '{product.tenant_id}' does not match repository context '{tenant_id}'"
            )
        key = format_tenant_cache_key(tenant_id, "product", product.product_id)
        with self._lock:
            self._products[key] = product

    def list_by_merchant(self, tenant_id: str, merchant_id: str) -> list[Product]:
        prefix = f"tenant:{tenant_id}:product:"
        with self._lock:
            return [
                p
                for k, p in self._products.items()
                if k.startswith(prefix) and p.merchant_id == merchant_id
            ]

    def search(self, tenant_id: str, query: str) -> list[Product]:
        q = query.strip().lower()
        prefix = f"tenant:{tenant_id}:product:"
        with self._lock:
            return [
                p
                for k, p in self._products.items()
                if k.startswith(prefix) and (q in p.name.lower() or q in p.category.lower())
            ]


class InMemoryVariantRepository(VariantRepository):
    """Thread-safe in-memory store for ProductVariants."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> variant_id -> ProductVariant
        self._variants: dict[str, dict[str, ProductVariant]] = {}

    def get(self, tenant_id: str, variant_id: str) -> ProductVariant | None:
        with self._lock:
            return self._variants.get(tenant_id, {}).get(variant_id)

    def save(self, tenant_id: str, variant: ProductVariant) -> None:
        with self._lock:
            tenant_store = self._variants.setdefault(tenant_id, {})
            tenant_store[variant.variant_id] = variant

    def list_by_product(self, tenant_id: str, product_id: str) -> list[ProductVariant]:
        with self._lock:
            tenant_store = self._variants.get(tenant_id, {})
            return [v for v in tenant_store.values() if v.product_id == product_id]


class InMemoryInventoryRepository(InventoryRepository):
    """Thread-safe in-memory store for Inventory with atomic reserve/release."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # storage key: format_tenant_cache_key(tenant_id, "inventory", variant_id)
        self._inventory: dict[str, Inventory] = {}

    def get(self, tenant_id: str, variant_id: str) -> Inventory | None:
        key = format_tenant_cache_key(tenant_id, "inventory", variant_id)
        with self._lock:
            return self._inventory.get(key)

    def save(self, tenant_id: str, inventory: Inventory) -> None:
        if inventory.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Inventory tenant '{inventory.tenant_id}' does not match context '{tenant_id}'"
            )
        key = format_tenant_cache_key(tenant_id, "inventory", inventory.variant_id)
        with self._lock:
            self._inventory[key] = inventory

    def reserve(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        key = format_tenant_cache_key(tenant_id, "inventory", variant_id)
        with self._lock:
            current = self._inventory.get(key)
            if current is None:
                raise InsufficientInventoryError(
                    f"No inventory record found for variant '{variant_id}' in tenant '{tenant_id}'"
                )
            updated = current.reserve(qty)
            self._inventory[key] = updated
            return updated

    def release(self, tenant_id: str, variant_id: str, qty: int) -> Inventory:
        key = format_tenant_cache_key(tenant_id, "inventory", variant_id)
        with self._lock:
            current = self._inventory.get(key)
            if current is None:
                raise InvalidCommerceError(
                    f"No inventory record found for variant '{variant_id}' in tenant '{tenant_id}'"
                )
            updated = current.release(qty)
            self._inventory[key] = updated
            return updated


class InMemoryMerchantRepository(MerchantRepository):
    """Thread-safe in-memory store for Merchants."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._merchants: dict[str, Merchant] = {}

    def get(self, tenant_id: str, merchant_id: str) -> Merchant | None:
        key = format_tenant_cache_key(tenant_id, "merchant", merchant_id)
        with self._lock:
            return self._merchants.get(key)

    def save(self, tenant_id: str, merchant: Merchant) -> None:
        if merchant.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Merchant tenant '{merchant.tenant_id}' does not match context '{tenant_id}'"
            )
        key = format_tenant_cache_key(tenant_id, "merchant", merchant.merchant_id)
        with self._lock:
            self._merchants[key] = merchant


class InMemoryEvidenceRepository(EvidenceRepository):
    """Thread-safe in-memory store for Evidence records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> evidence_id -> Evidence
        self._evidence: dict[str, dict[str, Evidence]] = {}

    def get(self, tenant_id: str, evidence_id: str) -> Evidence | None:
        with self._lock:
            return self._evidence.get(tenant_id, {}).get(evidence_id)

    def save(self, tenant_id: str, evidence: Evidence) -> None:
        if evidence.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Evidence tenant '{evidence.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._evidence.setdefault(tenant_id, {})
            tenant_store[evidence.evidence_id] = evidence

    def list_for_resource(self, tenant_id: str, resource_id: str) -> list[Evidence]:
        with self._lock:
            tenant_store = self._evidence.get(tenant_id, {})
            return [e for e in tenant_store.values() if e.resource_id == resource_id]

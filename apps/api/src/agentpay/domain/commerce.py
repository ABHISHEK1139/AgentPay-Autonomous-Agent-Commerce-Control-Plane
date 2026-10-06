"""Pure domain models for Commerce: Merchants, Products, Variants, and Inventory (§30, §37, §38).

Following Clean Architecture (§8, §10):
- Pure Python standard library and Money value object only.
- Zero external dependencies.
- Fail closed on invalid inventory or price conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentpay.domain.errors import (
    InsufficientInventoryError,
    InvalidCommerceError,
)
from agentpay.domain.money import Money


class MerchantStatus(StrEnum):
    """Lifecycle state of a registered merchant (§153)."""

    REGISTERED = "REGISTERED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class Merchant:
    """A commercial entity selling goods or services through the platform (§37)."""

    merchant_id: str
    tenant_id: str
    name: str
    status: MerchantStatus = MerchantStatus.ACTIVE
    allowed_agent_purchase: bool = True
    max_order_amount_minor: int | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("merchant_id", self.merchant_id),
            ("tenant_id", self.tenant_id),
            ("name", self.name),
        )
        for field_name, value in strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidCommerceError(f"{field_name} must be a non-empty string")

        status_val: object = self.status
        if isinstance(status_val, str) and not isinstance(status_val, MerchantStatus):
            try:
                object.__setattr__(self, "status", MerchantStatus(status_val))
            except ValueError as err:
                raise InvalidCommerceError(
                    f"status must be a MerchantStatus, got {status_val!r}"
                ) from err
        elif not isinstance(status_val, MerchantStatus):
            raise InvalidCommerceError(f"status must be a MerchantStatus, got {status_val!r}")

        max_amount: object = self.max_order_amount_minor
        if max_amount is not None and (
            isinstance(max_amount, bool) or not isinstance(max_amount, int) or max_amount < 0
        ):
            raise InvalidCommerceError(
                f"max_order_amount_minor must be None or a non-negative int, got {max_amount!r}"
            )

    @property
    def is_active(self) -> bool:
        return self.status is MerchantStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Product:
    """Catalog product entity within a merchant catalog (§30)."""

    product_id: str
    tenant_id: str
    merchant_id: str
    name: str
    category: str
    description: str

    def __post_init__(self) -> None:
        fields: tuple[tuple[str, object], ...] = (
            ("product_id", self.product_id),
            ("tenant_id", self.tenant_id),
            ("merchant_id", self.merchant_id),
            ("name", self.name),
            ("category", self.category),
            ("description", self.description),
        )
        for field_name, value in fields:
            if not isinstance(value, str) or not value.strip():
                raise InvalidCommerceError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ProductVariant:
    """A purchasable variant of a product with a fixed price (§30)."""

    variant_id: str
    product_id: str
    sku: str
    name: str
    price: Money

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("variant_id", self.variant_id),
            ("product_id", self.product_id),
            ("sku", self.sku),
            ("name", self.name),
        )
        for field_name, value in strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidCommerceError(f"{field_name} must be a non-empty string")

        if not isinstance(self.price, Money):
            raise InvalidCommerceError(
                f"price must be a Money instance, got {type(self.price).__name__}"
            )


@dataclass(frozen=True, slots=True)
class Inventory:
    """Inventory tracking for a specific product variant (§30, §44)."""

    variant_id: str
    tenant_id: str
    available: int
    reserved: int = 0

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("variant_id", self.variant_id),
            ("tenant_id", self.tenant_id),
        )
        for field_name, value in strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidCommerceError(f"{field_name} must be a non-empty string")

        for num_name, num_val in (("available", self.available), ("reserved", self.reserved)):
            val_obj: object = num_val
            if isinstance(val_obj, bool) or not isinstance(val_obj, int) or val_obj < 0:
                raise InvalidCommerceError(
                    f"{num_name} must be a non-negative int, got {num_val!r}"
                )

        if self.reserved > self.available:
            raise InvalidCommerceError(
                f"reserved ({self.reserved}) cannot exceed available ({self.available})"
            )

    @property
    def effective_available(self) -> int:
        return self.available - self.reserved

    def can_fulfill(self, qty: int) -> bool:
        qty_obj: object = qty
        if isinstance(qty_obj, bool) or not isinstance(qty_obj, int) or qty <= 0:
            return False
        return self.effective_available >= qty

    def reserve(self, qty: int) -> Inventory:
        qty_obj: object = qty
        if isinstance(qty_obj, bool) or not isinstance(qty_obj, int) or qty <= 0:
            raise InvalidCommerceError(f"Quantity to reserve must be a positive int, got {qty!r}")
        if not self.can_fulfill(qty):
            raise InsufficientInventoryError(
                f"Cannot reserve {qty} units; only {self.effective_available} effective units available"
            )
        return Inventory(
            variant_id=self.variant_id,
            tenant_id=self.tenant_id,
            available=self.available,
            reserved=self.reserved + qty,
        )

    def release(self, qty: int) -> Inventory:
        qty_obj: object = qty
        if isinstance(qty_obj, bool) or not isinstance(qty_obj, int) or qty <= 0:
            raise InvalidCommerceError(f"Quantity to release must be a positive int, got {qty!r}")
        if qty > self.reserved:
            raise InvalidCommerceError(
                f"Cannot release {qty} units; only {self.reserved} units are reserved"
            )
        return Inventory(
            variant_id=self.variant_id,
            tenant_id=self.tenant_id,
            available=self.available,
            reserved=self.reserved - qty,
        )

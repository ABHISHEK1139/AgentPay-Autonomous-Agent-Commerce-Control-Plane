"""Pure domain model for the multi-item Cart aggregate (§7, §38, ADR-0006).

Following Clean Architecture (§8, §10):
- Pure Python standard library only.
- Single-merchant MVP semantics: a cart binds to exactly one merchant.
- Fail closed on invalid quantities and on empty-string identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from agentpay.domain.errors import InvalidCommerceError


class CartStatus(StrEnum):
    """Lifecycle state of a shopping cart (ADR-0006)."""

    ACTIVE = "ACTIVE"
    CHECKED_OUT = "CHECKED_OUT"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class CartItem:
    """A single line item: a variant and a positive quantity (ADR-0006)."""

    variant_id: str
    quantity: int

    def __post_init__(self) -> None:
        variant_obj: object = self.variant_id
        if not isinstance(variant_obj, str) or not variant_obj.strip():
            raise InvalidCommerceError("variant_id must be a non-empty string")
        qty_obj: object = self.quantity
        if isinstance(qty_obj, bool) or not isinstance(qty_obj, int) or qty_obj <= 0:
            raise InvalidCommerceError(f"quantity must be a positive int, got {self.quantity!r}")


@dataclass(frozen=True, slots=True)
class Cart:
    """A mutable-by-replacement shopping cart bound to one merchant (ADR-0006).

    The cart is a *container*, never an authority: prices, availability, policy, and
    approvals are re-verified at checkout time against ground truth.
    """

    cart_id: str
    tenant_id: str
    principal_id: str
    agent_id: str
    merchant_id: str | None = None
    status: CartStatus = CartStatus.ACTIVE
    items: tuple[CartItem, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("cart_id", self.cart_id),
            ("tenant_id", self.tenant_id),
            ("principal_id", self.principal_id),
            ("agent_id", self.agent_id),
        )
        for field_name, value in strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidCommerceError(f"{field_name} must be a non-empty string")

        merchant_obj: object = self.merchant_id
        if merchant_obj is not None and (
            not isinstance(merchant_obj, str) or not merchant_obj.strip()
        ):
            raise InvalidCommerceError("merchant_id must be None or a non-empty string")

        status_val: object = self.status
        if isinstance(status_val, str) and not isinstance(status_val, CartStatus):
            try:
                object.__setattr__(self, "status", CartStatus(status_val))
            except ValueError as err:
                raise InvalidCommerceError(
                    f"status must be a CartStatus, got {status_val!r}"
                ) from err
        elif not isinstance(status_val, CartStatus):
            raise InvalidCommerceError(f"status must be a CartStatus, got {status_val!r}")

        for item in self.items:
            if not isinstance(item, CartItem):
                raise InvalidCommerceError(
                    f"items must contain CartItem instances, got {type(item).__name__}"
                )

        seen: set[str] = set()
        for item in self.items:
            if item.variant_id in seen:
                raise InvalidCommerceError(
                    f"duplicate variant '{item.variant_id}' in cart items; merge before constructing"
                )
            seen.add(item.variant_id)

        for timestamp_name, timestamp in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise InvalidCommerceError(f"{timestamp_name} must be timezone-aware (UTC)")

        if self.updated_at < self.created_at:
            raise InvalidCommerceError("updated_at cannot be earlier than created_at")

    @property
    def is_active(self) -> bool:
        return self.status is CartStatus.ACTIVE

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    def get_item(self, variant_id: str) -> CartItem | None:
        for item in self.items:
            if item.variant_id == variant_id:
                return item
        return None

    def add_item(
        self,
        variant_id: str,
        quantity: int,
        merchant_id: str | None = None,
        now: datetime | None = None,
    ) -> Cart:
        """Return a new cart with the item added, merging quantity for an existing variant.

        Fails closed when the cart is not ACTIVE or when the resolved merchant conflicts
        with the merchant already bound to the cart (single-merchant MVP, ADR-0006).
        """
        if not self.is_active:
            raise InvalidCommerceError(f"Cannot modify cart in status '{self.status.value}'")

        new_item = CartItem(variant_id=variant_id, quantity=quantity)
        existing = self.get_item(variant_id)
        if existing is not None:
            items = tuple(
                CartItem(variant_id=item.variant_id, quantity=item.quantity + new_item.quantity)
                if item.variant_id == variant_id
                else item
                for item in self.items
            )
        else:
            items = (*self.items, new_item)

        bound_merchant = self.merchant_id
        if merchant_id is not None:
            if bound_merchant is not None and bound_merchant != merchant_id:
                raise InvalidCommerceError(
                    f"Cart '{self.cart_id}' is bound to merchant '{bound_merchant}'; "
                    f"variant belongs to merchant '{merchant_id}' (single-merchant cart)"
                )
            bound_merchant = bound_merchant or merchant_id

        return replace(
            self, items=items, merchant_id=bound_merchant, updated_at=now or datetime.now(UTC)
        )

    def remove_item(self, variant_id: str, now: datetime | None = None) -> Cart:
        """Return a new cart with the item removed."""
        if not self.is_active:
            raise InvalidCommerceError(f"Cannot modify cart in status '{self.status.value}'")
        if self.get_item(variant_id) is None:
            raise InvalidCommerceError(f"Variant '{variant_id}' is not in cart '{self.cart_id}'")
        items = tuple(item for item in self.items if item.variant_id != variant_id)
        return replace(self, items=items, updated_at=now or datetime.now(UTC))

    def mark_checked_out(self, now: datetime | None = None) -> Cart:
        if self.status is not CartStatus.ACTIVE:
            raise InvalidCommerceError(f"Cannot check out cart in status '{self.status.value}'")
        return replace(self, status=CartStatus.CHECKED_OUT, updated_at=now or datetime.now(UTC))

    def abandon(self, now: datetime | None = None) -> Cart:
        if self.status is not CartStatus.ACTIVE:
            raise InvalidCommerceError(f"Cannot abandon cart in status '{self.status.value}'")
        return replace(self, status=CartStatus.ABANDONED, updated_at=now or datetime.now(UTC))

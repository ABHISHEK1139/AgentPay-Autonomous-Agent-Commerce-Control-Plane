"""Unit tests for the multi-item Cart domain aggregate (§7, §38, ADR-0006).

Verifies:
1. CartItem validation fails closed on empty identifiers and non-positive quantities.
2. Cart merge-on-add semantics, single-merchant enforcement, and removal.
3. Cart lifecycle transitions (ACTIVE -> CHECKED_OUT / ABANDONED) reject illegal changes.
4. Cart structural invariants: duplicate variants, timezone-aware timestamps, ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentpay.domain.cart import Cart, CartItem, CartStatus
from agentpay.domain.errors import InvalidCommerceError


def make_cart(
    merchant_id: str | None = "merch_01",
    items: tuple[CartItem, ...] = (),
    status: CartStatus = CartStatus.ACTIVE,
) -> Cart:
    return Cart(
        cart_id="cart_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        merchant_id=merchant_id,
        status=status,
        items=items,
    )


# ============================================================================
# 1. CartItem validation
# ============================================================================
def test_cart_item_accepts_valid_input() -> None:
    item = CartItem(variant_id="var_01", quantity=3)
    assert item.variant_id == "var_01"
    assert item.quantity == 3


@pytest.mark.parametrize("variant_id", ["", "   "])
def test_cart_item_rejects_blank_variant_id(variant_id: str) -> None:
    with pytest.raises(InvalidCommerceError):
        CartItem(variant_id=variant_id, quantity=1)


@pytest.mark.parametrize("quantity", [0, -1, -100])
def test_cart_item_rejects_non_positive_quantity(quantity: int) -> None:
    with pytest.raises(InvalidCommerceError):
        CartItem(variant_id="var_01", quantity=quantity)


@pytest.mark.parametrize("quantity", [True, False, 1.5, "2"])
def test_cart_item_rejects_non_integer_quantity(quantity: object) -> None:
    with pytest.raises(InvalidCommerceError):
        CartItem(variant_id="var_01", quantity=quantity)  # type: ignore[arg-type]


# ============================================================================
# 2. Cart add / merge / remove
# ============================================================================
def test_add_item_activates_single_merchant_binding() -> None:
    cart = make_cart(merchant_id=None)
    updated = cart.add_item(variant_id="var_01", quantity=2, merchant_id="merch_01")
    assert updated.merchant_id == "merch_01"
    assert updated.items == (CartItem(variant_id="var_01", quantity=2),)
    # Original cart is unchanged (immutable value semantics)
    assert cart.merchant_id is None
    assert cart.items == ()


def test_add_item_merges_duplicate_variant_quantities() -> None:
    cart = make_cart()
    cart = cart.add_item(variant_id="var_01", quantity=2, merchant_id="merch_01")
    cart = cart.add_item(variant_id="var_01", quantity=3, merchant_id="merch_01")
    assert cart.items == (CartItem(variant_id="var_01", quantity=5),)
    assert cart.total_quantity == 5


def test_add_item_rejects_mixed_merchant_cart() -> None:
    cart = make_cart()
    with pytest.raises(InvalidCommerceError, match="single-merchant cart"):
        cart.add_item(variant_id="var_01", quantity=1, merchant_id="merch_02")


def test_add_item_allows_unresolved_merchant_on_existing_binding() -> None:
    cart = make_cart(merchant_id="merch_01")
    updated = cart.add_item(variant_id="var_09", quantity=1)
    assert updated.merchant_id == "merch_01"
    assert updated.get_item("var_09") is not None


def test_remove_item_drops_only_the_target_variant() -> None:
    cart = make_cart()
    cart = cart.add_item(variant_id="var_01", quantity=1, merchant_id="merch_01")
    cart = cart.add_item(variant_id="var_02", quantity=4, merchant_id="merch_01")
    updated = cart.remove_item("var_01")
    assert updated.items == (CartItem(variant_id="var_02", quantity=4),)


def test_remove_item_rejects_unknown_variant() -> None:
    cart = make_cart()
    with pytest.raises(InvalidCommerceError, match="is not in cart"):
        cart.remove_item("var_missing")


def test_modifying_non_active_cart_fails_closed() -> None:
    cart = make_cart(status=CartStatus.CHECKED_OUT)
    with pytest.raises(InvalidCommerceError, match="Cannot modify cart"):
        cart.add_item(variant_id="var_01", quantity=1, merchant_id="merch_01")
    with pytest.raises(InvalidCommerceError, match="Cannot modify cart"):
        cart.remove_item("var_01")


# ============================================================================
# 3. Cart lifecycle transitions
# ============================================================================
def test_mark_checked_out_transitions_once() -> None:
    cart = make_cart()
    checked_out = cart.mark_checked_out()
    assert checked_out.status is CartStatus.CHECKED_OUT
    assert checked_out.is_active is False
    with pytest.raises(InvalidCommerceError, match="Cannot check out"):
        checked_out.mark_checked_out()


def test_abandon_only_from_active() -> None:
    cart = make_cart()
    abandoned = cart.abandon()
    assert abandoned.status is CartStatus.ABANDONED
    with pytest.raises(InvalidCommerceError, match="Cannot abandon"):
        abandoned.abandon()


# ============================================================================
# 4. Structural invariants
# ============================================================================
@pytest.mark.parametrize(
    "field_name",
    ["cart_id", "tenant_id", "principal_id", "agent_id"],
)
def test_cart_rejects_blank_identity_fields(field_name: str) -> None:
    kwargs: dict[str, object] = {
        "cart_id": "cart_01",
        "tenant_id": "tenant_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
    }
    kwargs[field_name] = "  "
    with pytest.raises(InvalidCommerceError, match=field_name):
        Cart(**kwargs)  # type: ignore[arg-type]


def test_cart_rejects_blank_merchant_id() -> None:
    with pytest.raises(InvalidCommerceError, match="merchant_id"):
        make_cart(merchant_id=" ")


def test_cart_rejects_duplicate_variant_items() -> None:
    with pytest.raises(InvalidCommerceError, match="duplicate variant"):
        make_cart(
            items=(
                CartItem(variant_id="var_01", quantity=1),
                CartItem(variant_id="var_01", quantity=2),
            )
        )


def test_cart_rejects_naive_timestamps() -> None:
    with pytest.raises(InvalidCommerceError, match="timezone-aware"):
        Cart(
            cart_id="cart_01",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            created_at=datetime(2026, 1, 1),  # noqa: DTZ001 - deliberately naive
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_cart_rejects_updated_at_before_created_at() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidCommerceError, match="updated_at"):
        Cart(
            cart_id="cart_01",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )


def test_cart_coerces_status_string_values() -> None:
    cart = Cart(
        cart_id="cart_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        status="ABANDONED",  # type: ignore[arg-type]
    )
    assert cart.status is CartStatus.ABANDONED


def test_cart_rejects_unknown_status_value() -> None:
    with pytest.raises(InvalidCommerceError, match="status"):
        Cart(
            cart_id="cart_01",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            status="NOT_A_STATUS",  # type: ignore[arg-type]
        )


def test_cart_rejects_non_cart_item_entries() -> None:
    with pytest.raises(InvalidCommerceError, match="CartItem"):
        Cart(
            cart_id="cart_01",
            tenant_id="tenant_01",
            principal_id="user_01",
            agent_id="agent_01",
            items=("var_01",),  # type: ignore[arg-type]
        )


def test_get_item_returns_none_for_unknown_variant() -> None:
    assert make_cart().get_item("var_missing") is None


def test_timezone_aware_timestamp_accepts_non_utc_offsets() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    cart = Cart(
        cart_id="cart_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        created_at=now,
        updated_at=now,
    )
    assert cart.created_at.tzinfo is not None

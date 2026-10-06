"""Unit and property tests for Commerce, Evidence, and Conflict Detection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryEvidenceRepository,
    InMemoryInventoryRepository,
    InMemoryMerchantRepository,
    InMemoryProductRepository,
    InMemoryVariantRepository,
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
    InvalidCommerceError,
    InvalidEvidenceError,
)
from agentpay.domain.evidence import (
    ConflictSeverity,
    ConflictType,
    Evidence,
    EvidenceConflictDetector,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceType,
    SponsorshipDisclosure,
)
from agentpay.domain.money import Money


def test_merchant_validation() -> None:
    m = Merchant(
        merchant_id="merch_01",
        tenant_id="tenant_01",
        name="Tech Retailer",
        status=MerchantStatus.ACTIVE,
        allowed_agent_purchase=True,
        max_order_amount_minor=500_000,
    )
    assert m.is_active is True
    assert m.merchant_id == "merch_01"

    with pytest.raises(InvalidCommerceError, match="merchant_id"):
        Merchant(merchant_id="", tenant_id="t1", name="M")

    with pytest.raises(InvalidCommerceError, match="max_order_amount_minor"):
        Merchant(merchant_id="m1", tenant_id="t1", name="M", max_order_amount_minor=-1)


def test_product_and_variant_validation() -> None:
    p = Product(
        product_id="prod_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        name="Pro Laptop",
        category="electronics",
        description="High-performance work laptop",
    )
    assert p.name == "Pro Laptop"

    v = ProductVariant(
        variant_id="var_01",
        product_id="prod_01",
        sku="SKU-16GB-512GB",
        name="16GB RAM / 512GB SSD",
        price=Money(149900, "INR"),
    )
    assert v.price.minor_units == 149900

    with pytest.raises(InvalidCommerceError, match="product_id"):
        Product(
            product_id="",
            tenant_id="t1",
            merchant_id="m1",
            name="P",
            category="c",
            description="d",
        )


def test_inventory_reserve_and_release() -> None:
    inv = Inventory(
        variant_id="var_01",
        tenant_id="tenant_01",
        available=10,
        reserved=2,
    )
    assert inv.effective_available == 8
    assert inv.can_fulfill(5) is True
    assert inv.can_fulfill(9) is False

    # Reserve 3 units
    inv_reserved = inv.reserve(3)
    assert inv_reserved.reserved == 5
    assert inv_reserved.effective_available == 5

    # Release 2 units
    inv_released = inv_reserved.release(2)
    assert inv_released.reserved == 3
    assert inv_released.effective_available == 7

    # Insufficient stock fails closed
    with pytest.raises(InsufficientInventoryError):
        inv.reserve(9)

    # Release more than reserved fails closed
    with pytest.raises(InvalidCommerceError):
        inv.release(5)


def test_evidence_and_sponsorship_models() -> None:
    source = EvidenceSource(type=EvidenceSourceType.MERCHANT_CATALOG, id="catalog_v1")
    now = datetime.now(UTC)

    ev = Evidence(
        evidence_id="evi_01",
        tenant_id="tenant_01",
        resource_id="prod_01",
        field_name="price",
        value="149900",
        evidence_type=EvidenceType.PRICE,
        source=source,
        observed_at=now,
    )
    assert ev.value == "149900"

    sponsor = SponsorshipDisclosure(
        resource_id="prod_01",
        is_sponsored=True,
        sponsor_name="Acme Corp",
        disclosure_text="Paid promotion",
    )
    assert sponsor.is_sponsored is True

    with pytest.raises(InvalidEvidenceError, match="resource_id"):
        SponsorshipDisclosure(resource_id="", is_sponsored=False)


def test_conflict_detector_price_mismatch() -> None:
    agent_price = Money(129900, "INR")
    merchant_price = Money(149900, "INR")

    conflict = EvidenceConflictDetector.detect_price_conflict(
        asserted_price=agent_price,
        merchant_price=merchant_price,
        tenant_id="tenant_01",
        resource_id="prod_01",
    )
    assert conflict is not None
    assert conflict.conflict_type == ConflictType.PRICE_MISMATCH
    assert conflict.severity == ConflictSeverity.CRITICAL
    assert conflict.asserted_value == str(agent_price)
    assert conflict.verified_value == str(merchant_price)

    # Matching price yields no conflict
    assert (
        EvidenceConflictDetector.detect_price_conflict(
            agent_price, agent_price, "tenant_01", "prod_01"
        )
        is None
    )


def test_conflict_detector_inventory_mismatch() -> None:
    # Insufficient stock for requested quantity
    conflict = EvidenceConflictDetector.detect_inventory_conflict(
        asserted_available=5,
        actual_available=2,
        requested_qty=3,
        tenant_id="tenant_01",
        resource_id="prod_01",
    )
    assert conflict is not None
    assert conflict.conflict_type == ConflictType.INVENTORY_MISMATCH
    assert conflict.severity == ConflictSeverity.CRITICAL

    # Quantity available but asserted count differs from actual count
    conflict_diff = EvidenceConflictDetector.detect_inventory_conflict(
        asserted_available=10,
        actual_available=8,
        requested_qty=2,
        tenant_id="tenant_01",
        resource_id="prod_01",
    )
    assert conflict_diff is not None
    assert conflict_diff.severity == ConflictSeverity.HIGH

    # Matching inventory
    assert (
        EvidenceConflictDetector.detect_inventory_conflict(
            asserted_available=8,
            actual_available=8,
            requested_qty=2,
            tenant_id="tenant_01",
            resource_id="prod_01",
        )
        is None
    )


def test_conflict_detector_specification_conflict() -> None:
    conflict = EvidenceConflictDetector.detect_specification_conflict(
        field_name="material",
        asserted_value="100% Cotton",
        verified_value="65% Polyester / 35% Cotton",
        tenant_id="tenant_01",
        resource_id="prod_01",
    )
    assert conflict is not None
    assert conflict.conflict_type == ConflictType.SPECIFICATION_CONFLICT
    assert conflict.severity == ConflictSeverity.HIGH

    # Matching specifications
    assert (
        EvidenceConflictDetector.detect_specification_conflict(
            field_name="material",
            asserted_value="Cotton",
            verified_value="cotton",
            tenant_id="tenant_01",
            resource_id="prod_01",
        )
        is None
    )


def test_conflict_detector_detect_all_conflicts() -> None:
    conflicts = EvidenceConflictDetector.detect_all_conflicts(
        tenant_id="tenant_01",
        resource_id="prod_01",
        asserted_price=Money(100, "INR"),
        merchant_price=Money(200, "INR"),
        asserted_available=1,
        actual_available=0,
        requested_qty=1,
        specifications=[("color", "red", "blue")],
    )
    assert len(conflicts) == 3


def test_in_memory_commerce_repositories() -> None:
    t1 = "tenant_01"
    t2 = "tenant_02"

    product_repo = InMemoryProductRepository()
    variant_repo = InMemoryVariantRepository()
    inventory_repo = InMemoryInventoryRepository()
    merchant_repo = InMemoryMerchantRepository()
    evidence_repo = InMemoryEvidenceRepository()

    # Merchant
    m = Merchant(merchant_id="m1", tenant_id=t1, name="Store 1")
    merchant_repo.save(t1, m)
    assert merchant_repo.get(t1, "m1") == m
    assert merchant_repo.get(t2, "m1") is None

    # Cross-tenant save rejection
    with pytest.raises(CrossTenantAccessError):
        merchant_repo.save(t2, m)

    # Product & Search
    p1 = Product(
        product_id="p1",
        tenant_id=t1,
        merchant_id="m1",
        name="Super Laptop",
        category="computers",
        description="Fast",
    )
    product_repo.save(t1, p1)
    assert product_repo.get(t1, "p1") == p1
    assert product_repo.get(t2, "p1") is None
    assert len(product_repo.search(t1, "laptop")) == 1
    assert len(product_repo.search(t2, "laptop")) == 0

    # Variant
    v1 = ProductVariant(
        variant_id="v1", product_id="p1", sku="SKU1", name="Base", price=Money(500, "INR")
    )
    variant_repo.save(t1, v1)
    assert variant_repo.get(t1, "v1") == v1
    assert len(variant_repo.list_by_product(t1, "p1")) == 1

    # Inventory atomic reserve and release
    inv1 = Inventory(variant_id="v1", tenant_id=t1, available=10, reserved=0)
    inventory_repo.save(t1, inv1)

    reserved_inv = inventory_repo.reserve(t1, "v1", 4)
    assert reserved_inv.reserved == 4
    assert inventory_repo.get(t1, "v1") == reserved_inv

    released_inv = inventory_repo.release(t1, "v1", 2)
    assert released_inv.reserved == 2

    # Evidence
    ev = Evidence(
        evidence_id="e1",
        tenant_id=t1,
        resource_id="p1",
        field_name="specs",
        value="16GB",
        evidence_type=EvidenceType.SPECIFICATION,
        source=EvidenceSource(type=EvidenceSourceType.MANUAL_ENTRY, id="audit"),
        observed_at=datetime.now(UTC),
    )
    evidence_repo.save(t1, ev)
    assert evidence_repo.get(t1, "e1") == ev
    assert len(evidence_repo.list_for_resource(t1, "p1")) == 1
    assert len(evidence_repo.list_for_resource(t2, "p1")) == 0


@given(
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=500),
)
def test_hypothesis_inventory_reserve_release_invariant(
    initial_available: int,
    reserve_qty: int,
) -> None:
    inv = Inventory(variant_id="v1", tenant_id="t1", available=initial_available)
    if inv.can_fulfill(reserve_qty):
        reserved = inv.reserve(reserve_qty)
        assert reserved.reserved == reserve_qty
        assert reserved.effective_available == initial_available - reserve_qty

        # Releasing full quantity restores effective available
        released = reserved.release(reserve_qty)
        assert released.reserved == 0
        assert released.effective_available == initial_available
    else:
        with pytest.raises(InsufficientInventoryError):
            inv.reserve(reserve_qty)

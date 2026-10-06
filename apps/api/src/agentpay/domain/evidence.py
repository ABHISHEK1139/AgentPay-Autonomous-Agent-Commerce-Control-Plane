"""Pure domain models for Evidence Provenance, Bias/Sponsorship Disclosures, and Conflict Detection (§32, §33, §34).

Following Clean Architecture (§8, §10):
- Pure Python standard library and domain Money object only.
- Deterministic conflict detection failing closed on critical price or stock discrepancies (§2.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from agentpay.domain.errors import InvalidEvidenceError
from agentpay.domain.money import Money


class EvidenceType(StrEnum):
    """Categorization of evidence provenance (§32)."""

    PRICE = "PRICE"
    INVENTORY = "INVENTORY"
    SPECIFICATION = "SPECIFICATION"
    RATING = "RATING"
    REVIEW = "REVIEW"
    IMAGE = "IMAGE"
    SHIPPING = "SHIPPING"
    RETURN_POLICY = "RETURN_POLICY"
    MERCHANT = "MERCHANT"
    PROMOTION = "PROMOTION"
    SPONSORSHIP = "SPONSORSHIP"


class EvidenceSourceType(StrEnum):
    """Origin of captured evidence."""

    MERCHANT_CATALOG = "MERCHANT_CATALOG"
    AGENT_ASSERTION = "AGENT_ASSERTION"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    EXTERNAL_API = "EXTERNAL_API"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """The origin entity providing evidence (§32)."""

    type: EvidenceSourceType
    id: str
    name: str | None = None

    def __post_init__(self) -> None:
        id_val: object = self.id
        if not isinstance(id_val, str) or not id_val.strip():
            raise InvalidEvidenceError("source.id must be a non-empty string")
        name_val: object = self.name
        if name_val is not None and (not isinstance(name_val, str) or not name_val.strip()):
            raise InvalidEvidenceError("source.name must be None or a non-empty string")


@dataclass(frozen=True, slots=True)
class Evidence:
    """An immutable observed evidence record supporting a claim or product attribute (§32)."""

    evidence_id: str
    tenant_id: str
    resource_id: str
    field_name: str
    value: str
    evidence_type: EvidenceType
    source: EvidenceSource
    observed_at: datetime

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("evidence_id", self.evidence_id),
            ("tenant_id", self.tenant_id),
            ("resource_id", self.resource_id),
            ("field_name", self.field_name),
            ("value", self.value),
        )
        for name, val in strings:
            if not isinstance(val, str) or not val.strip():
                raise InvalidEvidenceError(f"{name} must be a non-empty string")

        if not isinstance(self.source, EvidenceSource):
            raise InvalidEvidenceError("source must be an EvidenceSource instance")

        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise InvalidEvidenceError("observed_at must be timezone-aware (UTC)")


class ConflictType(StrEnum):
    """Classification of discrepancies discovered during validation (§33)."""

    PRICE_MISMATCH = "PRICE_MISMATCH"
    INVENTORY_MISMATCH = "INVENTORY_MISMATCH"
    SPECIFICATION_CONFLICT = "SPECIFICATION_CONFLICT"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"


class ConflictSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    """A detected conflict between agent assertions and verified ground truth (§33)."""

    conflict_id: str
    tenant_id: str
    resource_id: str
    conflict_type: ConflictType
    severity: ConflictSeverity
    field_name: str
    asserted_value: str
    verified_value: str
    asserted_source: EvidenceSource
    verified_source: EvidenceSource
    detected_at: datetime

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("conflict_id", self.conflict_id),
            ("tenant_id", self.tenant_id),
            ("resource_id", self.resource_id),
            ("field_name", self.field_name),
            ("asserted_value", self.asserted_value),
            ("verified_value", self.verified_value),
        )
        for name, val in strings:
            if not isinstance(val, str) or not val.strip():
                raise InvalidEvidenceError(f"{name} must be a non-empty string")

        if not isinstance(self.asserted_source, EvidenceSource):
            raise InvalidEvidenceError("asserted_source must be an EvidenceSource")
        if not isinstance(self.verified_source, EvidenceSource):
            raise InvalidEvidenceError("verified_source must be an EvidenceSource")

        if self.detected_at.tzinfo is None or self.detected_at.utcoffset() is None:
            raise InvalidEvidenceError("detected_at must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class SponsorshipDisclosure:
    """Explicit disclosure of sponsored placement or commercial affinity (§34, §155)."""

    resource_id: str
    is_sponsored: bool
    sponsor_name: str | None = None
    disclosure_text: str | None = None

    def __post_init__(self) -> None:
        res_id_val: object = self.resource_id
        if not isinstance(res_id_val, str) or not res_id_val.strip():
            raise InvalidEvidenceError("resource_id must be a non-empty string")
        is_spon_val: object = self.is_sponsored
        if not isinstance(is_spon_val, bool):
            raise InvalidEvidenceError("is_sponsored must be a boolean")


class EvidenceConflictDetector:
    """Deterministic detection of discrepancies between asserted and ground truth state (§33)."""

    @staticmethod
    def detect_price_conflict(
        asserted_price: Money,
        merchant_price: Money,
        tenant_id: str,
        resource_id: str,
        asserted_source: EvidenceSource | None = None,
        verified_source: EvidenceSource | None = None,
    ) -> EvidenceConflict | None:
        if (
            asserted_price.currency != merchant_price.currency
            or asserted_price.minor_units != merchant_price.minor_units
        ):
            return EvidenceConflict(
                conflict_id=f"cnf_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                resource_id=resource_id,
                conflict_type=ConflictType.PRICE_MISMATCH,
                severity=ConflictSeverity.CRITICAL,
                field_name="price",
                asserted_value=str(asserted_price),
                verified_value=str(merchant_price),
                asserted_source=asserted_source
                or EvidenceSource(type=EvidenceSourceType.AGENT_ASSERTION, id="agent"),
                verified_source=verified_source
                or EvidenceSource(type=EvidenceSourceType.MERCHANT_CATALOG, id="merchant"),
                detected_at=datetime.now(UTC),
            )
        return None

    @staticmethod
    def detect_inventory_conflict(
        asserted_available: int | None,
        actual_available: int,
        requested_qty: int,
        tenant_id: str,
        resource_id: str,
    ) -> EvidenceConflict | None:
        if actual_available < requested_qty:
            return EvidenceConflict(
                conflict_id=f"cnf_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                resource_id=resource_id,
                conflict_type=ConflictType.INVENTORY_MISMATCH,
                severity=ConflictSeverity.CRITICAL,
                field_name="inventory",
                asserted_value=f"fulfill_requested_{requested_qty}",
                verified_value=f"available_{actual_available}",
                asserted_source=EvidenceSource(type=EvidenceSourceType.AGENT_ASSERTION, id="agent"),
                verified_source=EvidenceSource(
                    type=EvidenceSourceType.MERCHANT_CATALOG, id="inventory"
                ),
                detected_at=datetime.now(UTC),
            )
        if asserted_available is not None and asserted_available != actual_available:
            return EvidenceConflict(
                conflict_id=f"cnf_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                resource_id=resource_id,
                conflict_type=ConflictType.INVENTORY_MISMATCH,
                severity=ConflictSeverity.HIGH,
                field_name="inventory",
                asserted_value=str(asserted_available),
                verified_value=str(actual_available),
                asserted_source=EvidenceSource(type=EvidenceSourceType.AGENT_ASSERTION, id="agent"),
                verified_source=EvidenceSource(
                    type=EvidenceSourceType.MERCHANT_CATALOG, id="inventory"
                ),
                detected_at=datetime.now(UTC),
            )
        return None

    @staticmethod
    def detect_specification_conflict(
        field_name: str,
        asserted_value: str,
        verified_value: str,
        tenant_id: str,
        resource_id: str,
    ) -> EvidenceConflict | None:
        if asserted_value.strip().lower() != verified_value.strip().lower():
            return EvidenceConflict(
                conflict_id=f"cnf_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                resource_id=resource_id,
                conflict_type=ConflictType.SPECIFICATION_CONFLICT,
                severity=ConflictSeverity.HIGH,
                field_name=field_name,
                asserted_value=asserted_value,
                verified_value=verified_value,
                asserted_source=EvidenceSource(type=EvidenceSourceType.AGENT_ASSERTION, id="agent"),
                verified_source=EvidenceSource(
                    type=EvidenceSourceType.MERCHANT_CATALOG, id="catalog"
                ),
                detected_at=datetime.now(UTC),
            )
        return None

    @staticmethod
    def detect_all_conflicts(
        tenant_id: str,
        resource_id: str,
        asserted_price: Money | None = None,
        merchant_price: Money | None = None,
        asserted_available: int | None = None,
        actual_available: int | None = None,
        requested_qty: int = 1,
        specifications: list[tuple[str, str, str]] | None = None,
    ) -> tuple[EvidenceConflict, ...]:
        conflicts: list[EvidenceConflict] = []
        if asserted_price is not None and merchant_price is not None:
            c = EvidenceConflictDetector.detect_price_conflict(
                asserted_price, merchant_price, tenant_id, resource_id
            )
            if c is not None:
                conflicts.append(c)

        if actual_available is not None:
            c = EvidenceConflictDetector.detect_inventory_conflict(
                asserted_available, actual_available, requested_qty, tenant_id, resource_id
            )
            if c is not None:
                conflicts.append(c)

        if specifications:
            for field_name, asserted_val, verified_val in specifications:
                c = EvidenceConflictDetector.detect_specification_conflict(
                    field_name, asserted_val, verified_val, tenant_id, resource_id
                )
                if c is not None:
                    conflicts.append(c)

        return tuple(conflicts)

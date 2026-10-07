"""Pure domain models for Human Approval workflows and exact binding verification (§20, §66).

Following Clean Architecture (§8, §10):
- Pure Python standard library and domain Money/errors only.
- Strict binding of approval to action, amount, resource, currency, action_version, and tenant.
- State machine: PENDING -> APPROVED | REJECTED | CANCELLED | EXPIRED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    InvalidApprovalError,
    InvalidApprovalTransitionError,
)
from agentpay.domain.money import Money


class ApprovalStatus(StrEnum):
    """Lifecycle status of a human approval request (§20)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Immutable cryptographically/logically bound scope of an approval (§20, §0.4).

    Approval binds to the EXACT action_id, amount, merchant, resource, currency,
    action version, and tenant. An approval granted for ₹5,000 never authorizes ₹50,000.
    """

    action_id: str
    amount: Money
    merchant_id: str
    resource_id: str
    currency: str
    action_version: int
    tenant_id: str

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("action_id", self.action_id),
            ("merchant_id", self.merchant_id),
            ("resource_id", self.resource_id),
            ("currency", self.currency),
            ("tenant_id", self.tenant_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidApprovalError(f"binding.{name} must be a non-empty string")

        if not isinstance(self.amount, Money):
            raise InvalidApprovalError(
                f"binding.amount must be a Money instance, got {type(self.amount).__name__}"
            )

        if self.amount.currency != self.currency:
            raise InvalidApprovalError(
                f"binding currency '{self.currency}' does not match amount currency '{self.amount.currency}'"
            )

        ver_obj: object = self.action_version
        if isinstance(ver_obj, bool) or not isinstance(ver_obj, int) or ver_obj < 1:
            raise InvalidApprovalError(
                f"binding.action_version must be a positive int, got {ver_obj!r}"
            )

    def matches(
        self,
        action_id: str,
        amount: Money,
        merchant_id: str,
        resource_id: str,
        currency: str,
        action_version: int,
        tenant_id: str,
    ) -> bool:
        """Check whether this binding matches the execution request exactly (§20)."""
        return (
            self.action_id == action_id
            and self.amount == amount
            and self.merchant_id == merchant_id
            and self.resource_id == resource_id
            and self.currency == currency
            and self.action_version == action_version
            and self.tenant_id == tenant_id
        )

    def assert_exact_match(
        self,
        action_id: str,
        amount: Money,
        merchant_id: str,
        resource_id: str,
        currency: str,
        action_version: int,
        tenant_id: str,
    ) -> None:
        """Assert exact match or raise ApprovalBindingMismatchError (§0.4, §20)."""
        if self.tenant_id != tenant_id:
            raise ApprovalBindingMismatchError(
                f"Tenant mismatch: approval bound to '{self.tenant_id}', got '{tenant_id}'"
            )
        if self.action_id != action_id:
            raise ApprovalBindingMismatchError(
                f"Action ID mismatch: approval bound to '{self.action_id}', got '{action_id}'"
            )
        if self.action_version != action_version:
            raise ApprovalBindingMismatchError(
                f"Action version mismatch: approval bound to v{self.action_version}, got v{action_version}"
            )
        if self.merchant_id != merchant_id:
            raise ApprovalBindingMismatchError(
                f"Merchant mismatch: approval bound to '{self.merchant_id}', got '{merchant_id}'"
            )
        if self.resource_id != resource_id:
            raise ApprovalBindingMismatchError(
                f"Resource mismatch: approval bound to '{self.resource_id}', got '{resource_id}'"
            )
        if self.currency != currency or self.amount.currency != currency:
            raise ApprovalBindingMismatchError(
                f"Currency mismatch: approval bound to '{self.currency}', got '{currency}'"
            )
        if self.amount != amount:
            raise ApprovalBindingMismatchError(
                f"Amount mismatch: approval bound to {self.amount}, got {amount}"
            )


@dataclass(frozen=True, slots=True)
class Approval:
    """An individual human approval record bound to an ActionRequest (§20)."""

    approval_id: str
    tenant_id: str
    binding: ApprovalBinding
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approver_id: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("approval_id", self.approval_id),
            ("tenant_id", self.tenant_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidApprovalError(f"{name} must be a non-empty string")

        if not isinstance(self.binding, ApprovalBinding):
            raise InvalidApprovalError("binding must be an ApprovalBinding instance")

        if self.binding.tenant_id != self.tenant_id:
            raise InvalidApprovalError("binding tenant_id must match approval tenant_id")

        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise InvalidApprovalError("requested_at must be timezone-aware (UTC)")

        if self.decided_at is not None and (
            self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None
        ):
            raise InvalidApprovalError("decided_at must be timezone-aware (UTC)")

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    @property
    def is_approved(self) -> bool:
        return self.status is ApprovalStatus.APPROVED

    @property
    def is_consumed(self) -> bool:
        return self.status is ApprovalStatus.CONSUMED

    def approve(self, approver_id: str, decided_at: datetime | None = None) -> Approval:
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(
                f"Cannot approve from status '{self.status.value}'; must be PENDING"
            )
        approver_obj: object = approver_id
        if not isinstance(approver_obj, str) or not approver_obj.strip():
            raise InvalidApprovalError("approver_id must be a non-empty string")

        now = decided_at or datetime.now(UTC)
        return Approval(
            approval_id=self.approval_id,
            tenant_id=self.tenant_id,
            binding=self.binding,
            status=ApprovalStatus.APPROVED,
            requested_at=self.requested_at,
            approver_id=approver_id,
            decided_at=now,
            reason=self.reason,
        )

    def reject(self, approver_id: str, reason: str, decided_at: datetime | None = None) -> Approval:
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(
                f"Cannot reject from status '{self.status.value}'; must be PENDING"
            )
        approver_obj: object = approver_id
        if not isinstance(approver_obj, str) or not approver_obj.strip():
            raise InvalidApprovalError("approver_id must be a non-empty string")

        reason_obj: object = reason
        if not isinstance(reason_obj, str) or not reason_obj.strip():
            raise InvalidApprovalError("rejection reason must be a non-empty string")

        now = decided_at or datetime.now(UTC)
        return Approval(
            approval_id=self.approval_id,
            tenant_id=self.tenant_id,
            binding=self.binding,
            status=ApprovalStatus.REJECTED,
            requested_at=self.requested_at,
            approver_id=approver_id,
            decided_at=now,
            reason=reason,
        )

    def cancel(self, decided_at: datetime | None = None) -> Approval:
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(
                f"Cannot cancel from status '{self.status.value}'; must be PENDING"
            )
        now = decided_at or datetime.now(UTC)
        return Approval(
            approval_id=self.approval_id,
            tenant_id=self.tenant_id,
            binding=self.binding,
            status=ApprovalStatus.CANCELLED,
            requested_at=self.requested_at,
            approver_id=self.approver_id,
            decided_at=now,
            reason="Cancelled by requester or system",
        )

    def expire(self, decided_at: datetime | None = None) -> Approval:
        if self.status is not ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(
                f"Cannot expire from status '{self.status.value}'; must be PENDING"
            )
        now = decided_at or datetime.now(UTC)
        return Approval(
            approval_id=self.approval_id,
            tenant_id=self.tenant_id,
            binding=self.binding,
            status=ApprovalStatus.EXPIRED,
            requested_at=self.requested_at,
            approver_id=self.approver_id,
            decided_at=now,
            reason="Approval window expired",
        )

    def consume(self, consumed_at: datetime | None = None) -> Approval:
        if self.status is not ApprovalStatus.APPROVED:
            raise InvalidApprovalTransitionError(
                f"Cannot consume approval with status '{self.status.value}'; must be APPROVED"
            )
        now = consumed_at or datetime.now(UTC)
        return Approval(
            approval_id=self.approval_id,
            tenant_id=self.tenant_id,
            binding=self.binding,
            status=ApprovalStatus.CONSUMED,
            requested_at=self.requested_at,
            approver_id=self.approver_id,
            decided_at=self.decided_at or now,
            reason=self.reason,
        )


@dataclass(frozen=True, slots=True)
class ApprovalTier:
    """Approval tier based on transaction amount threshold (§20)."""

    threshold_minor: int
    approver_role: str

    def __post_init__(self) -> None:
        thresh_obj: object = self.threshold_minor
        if isinstance(thresh_obj, bool) or not isinstance(thresh_obj, int) or thresh_obj < 0:
            raise InvalidApprovalError("threshold_minor must be a non-negative int")
        role_obj: object = self.approver_role
        if not isinstance(role_obj, str) or not role_obj.strip():
            raise InvalidApprovalError("approver_role must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """Multi-tier approval routing policy (§20)."""

    tiers: tuple[ApprovalTier, ...]
    policy_version: str

    def __post_init__(self) -> None:
        pol_ver: object = self.policy_version
        if not isinstance(pol_ver, str) or not pol_ver.strip():
            raise InvalidApprovalError("policy_version must be a non-empty string")

    def required_tier(self, amount_minor: int) -> ApprovalTier | None:
        """Find the tier required for an amount. Returns highest matching tier."""
        matching: list[ApprovalTier] = [t for t in self.tiers if amount_minor >= t.threshold_minor]
        if not matching:
            return None
        return max(matching, key=lambda t: t.threshold_minor)


class ApprovalAlreadyConsumedError(ApprovalBindingMismatchError):
    """Raised when attempting to execute an approval that has already been consumed (§20)."""

    code = "APPROVAL_ALREADY_CONSUMED"

"""Pure domain models for Payments, Orders, Idempotency, and Saga Orchestration (§39, §41, §42, §44).

Following Clean Architecture (§8, §10):
- Pure Python standard library and Money value object only.
- Strict separation of PaymentState and OrderState (§41).
- Immutable financial records and explicit compensation support (§44).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from agentpay.domain.errors import (
    InvalidPaymentError,
)
from agentpay.domain.money import Money


class PaymentStatus(StrEnum):
    """Isolated lifecycle state of a payment (§41).

    Order status is strictly decoupled from Payment status.
    """

    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    DECLINED = "DECLINED"
    UNKNOWN = "UNKNOWN"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class OrderStatus(StrEnum):
    """Isolated lifecycle state of a commercial order (§41)."""

    DRAFT = "DRAFT"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    FULFILLING = "FULFILLING"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


@dataclass(frozen=True, slots=True)
class PaymentAttempt:
    """An immutable record of a specific payment execution attempt (§41, §42)."""

    attempt_id: str
    intent_id: str
    tenant_id: str
    amount: Money
    status: PaymentStatus
    idempotency_key: str
    provider: str
    provider_payment_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("attempt_id", self.attempt_id),
            ("intent_id", self.intent_id),
            ("tenant_id", self.tenant_id),
            ("idempotency_key", self.idempotency_key),
            ("provider", self.provider),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        if not isinstance(self.amount, Money):
            raise InvalidPaymentError("amount must be a Money instance")

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidPaymentError("created_at must be timezone-aware (UTC)")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise InvalidPaymentError("updated_at must be timezone-aware (UTC)")

    @property
    def is_successful(self) -> bool:
        return self.status in (PaymentStatus.CAPTURED, PaymentStatus.AUTHORIZED)

    def transition_to(
        self,
        new_status: PaymentStatus,
        failure_reason: str | None = None,
        provider_payment_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> PaymentAttempt:
        now = updated_at or datetime.now(UTC)
        return PaymentAttempt(
            attempt_id=self.attempt_id,
            intent_id=self.intent_id,
            tenant_id=self.tenant_id,
            amount=self.amount,
            status=new_status,
            idempotency_key=self.idempotency_key,
            provider=self.provider,
            provider_payment_id=provider_payment_id or self.provider_payment_id,
            created_at=self.created_at,
            updated_at=now,
            failure_reason=failure_reason or self.failure_reason,
        )


@dataclass(frozen=True, slots=True)
class Refund:
    """An immutable record of a financial refund (§41, §44)."""

    refund_id: str
    payment_attempt_id: str
    tenant_id: str
    amount: Money
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("refund_id", self.refund_id),
            ("payment_attempt_id", self.payment_attempt_id),
            ("tenant_id", self.tenant_id),
            ("reason", self.reason),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        if not isinstance(self.amount, Money):
            raise InvalidPaymentError("amount must be a Money instance")

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidPaymentError("created_at must be timezone-aware (UTC)")


@dataclass(frozen=True, slots=True)
class Order:
    """An order entity representing the commercial commitment (§41, §44)."""

    order_id: str
    tenant_id: str
    merchant_id: str
    buyer_id: str
    agent_id: str
    variant_id: str
    quantity: int
    total_amount: Money
    status: OrderStatus = OrderStatus.PENDING_PAYMENT
    payment_attempt_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("order_id", self.order_id),
            ("tenant_id", self.tenant_id),
            ("merchant_id", self.merchant_id),
            ("buyer_id", self.buyer_id),
            ("agent_id", self.agent_id),
            ("variant_id", self.variant_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        qty_obj: object = self.quantity
        if isinstance(qty_obj, bool) or not isinstance(qty_obj, int) or qty_obj <= 0:
            raise InvalidPaymentError(f"quantity must be a positive int, got {qty_obj!r}")

        if not isinstance(self.total_amount, Money):
            raise InvalidPaymentError("total_amount must be a Money instance")

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidPaymentError("created_at must be timezone-aware (UTC)")

    def confirm(self, payment_attempt_id: str) -> Order:
        if self.status is not OrderStatus.PENDING_PAYMENT:
            raise InvalidPaymentError(f"Cannot confirm order in status '{self.status.value}'")
        return Order(
            order_id=self.order_id,
            tenant_id=self.tenant_id,
            merchant_id=self.merchant_id,
            buyer_id=self.buyer_id,
            agent_id=self.agent_id,
            variant_id=self.variant_id,
            quantity=self.quantity,
            total_amount=self.total_amount,
            status=OrderStatus.CONFIRMED,
            payment_attempt_id=payment_attempt_id,
            created_at=self.created_at,
        )

    def cancel(self, reason: str | None = None) -> Order:
        return Order(
            order_id=self.order_id,
            tenant_id=self.tenant_id,
            merchant_id=self.merchant_id,
            buyer_id=self.buyer_id,
            agent_id=self.agent_id,
            variant_id=self.variant_id,
            quantity=self.quantity,
            total_amount=self.total_amount,
            status=OrderStatus.CANCELLED,
            payment_attempt_id=self.payment_attempt_id,
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Record storing previously computed result for an operation key (§42)."""

    key: str
    tenant_id: str
    operation_type: str
    request_hash: str
    result_data: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("key", self.key),
            ("tenant_id", self.tenant_id),
            ("operation_type", self.operation_type),
            ("request_hash", self.request_hash),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        if not isinstance(self.result_data, dict):
            raise InvalidPaymentError("result_data must be a dict")

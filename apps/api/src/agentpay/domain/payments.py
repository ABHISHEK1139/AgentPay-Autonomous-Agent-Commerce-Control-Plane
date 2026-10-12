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
from typing import Any, Final

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


_ALLOWED_PAYMENT_TRANSITIONS: Final[dict[PaymentStatus, frozenset[PaymentStatus]]] = {
    PaymentStatus.CREATED: frozenset(
        {
            PaymentStatus.AUTHORIZED,
            PaymentStatus.PENDING,
            PaymentStatus.CAPTURED,
            PaymentStatus.FAILED,
            PaymentStatus.DECLINED,
            PaymentStatus.UNKNOWN,
        }
    ),
    PaymentStatus.PENDING: frozenset(
        {
            PaymentStatus.AUTHORIZED,
            PaymentStatus.CAPTURED,
            PaymentStatus.FAILED,
            PaymentStatus.DECLINED,
            PaymentStatus.UNKNOWN,
        }
    ),
    PaymentStatus.AUTHORIZED: frozenset(
        {
            PaymentStatus.CAPTURED,
            PaymentStatus.FAILED,
            PaymentStatus.DECLINED,
            PaymentStatus.REFUNDED,
            PaymentStatus.UNKNOWN,
        }
    ),
    PaymentStatus.CAPTURED: frozenset(
        {
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }
    ),
    PaymentStatus.PARTIALLY_REFUNDED: frozenset(
        {
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }
    ),
    PaymentStatus.UNKNOWN: frozenset(
        {
            PaymentStatus.AUTHORIZED,
            PaymentStatus.CAPTURED,
            PaymentStatus.FAILED,
            PaymentStatus.DECLINED,
        }
    ),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.DECLINED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}


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

    def can_transition_to(self, new_status: PaymentStatus) -> bool:
        """Predicate checking whether state transition is permitted by lifecycle state machine (§41, §125)."""
        if new_status == self.status:
            return True
        return new_status in _ALLOWED_PAYMENT_TRANSITIONS.get(self.status, frozenset())

    def transition_to(
        self,
        new_status: PaymentStatus,
        failure_reason: str | None = None,
        provider_payment_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> PaymentAttempt:
        if new_status != self.status and not self.can_transition_to(new_status):
            raise InvalidPaymentError(
                f"Illegal payment transition from {self.status.value} to {new_status.value}"
            )
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


@dataclass(frozen=True, slots=True)
class ProviderTransactionMapping:
    """Immutable mapping between external provider transaction identifiers and internal domain entities (§39, §41, §49)."""

    mapping_id: str
    tenant_id: str
    attempt_id: str
    provider: str
    provider_payment_id: str
    provider_order_id: str | None = None
    provider_account_id: str | None = None
    order_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("mapping_id", self.mapping_id),
            ("tenant_id", self.tenant_id),
            ("attempt_id", self.attempt_id),
            ("provider", self.provider),
            ("provider_payment_id", self.provider_payment_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidPaymentError("created_at must be timezone-aware (UTC)")


class WebhookEventStatus(StrEnum):
    """Lifecycle status of an ingested payment provider webhook event (§42, §61, AP-003)."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


_ALLOWED_WEBHOOK_EVENT_TRANSITIONS: Final[
    dict[WebhookEventStatus, frozenset[WebhookEventStatus]]
] = {
    WebhookEventStatus.RECEIVED: frozenset(
        {
            WebhookEventStatus.PROCESSING,
            WebhookEventStatus.FAILED,
            WebhookEventStatus.QUARANTINED,
        }
    ),
    WebhookEventStatus.PROCESSING: frozenset(
        {
            WebhookEventStatus.PROCESSED,
            WebhookEventStatus.FAILED,
            WebhookEventStatus.QUARANTINED,
        }
    ),
    WebhookEventStatus.FAILED: frozenset(
        {
            WebhookEventStatus.PROCESSING,
            WebhookEventStatus.QUARANTINED,
        }
    ),
    WebhookEventStatus.QUARANTINED: frozenset(
        {
            WebhookEventStatus.PROCESSING,
        }
    ),
    WebhookEventStatus.PROCESSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ProviderWebhookEvent:
    """Durable record of an external payment provider webhook event (§42, §61, AP-003)."""

    id: str
    provider: str
    event_id: str
    status: WebhookEventStatus = WebhookEventStatus.RECEIVED
    provider_account_id: str = ""
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt_count: int = 1
    last_error_code: str | None = None
    entity_reference: str | None = None
    event_type: str | None = None
    payload_hash: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("id", self.id),
            ("provider", self.provider),
            ("event_id", self.event_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidPaymentError(f"{name} must be a non-empty string")

        acc_obj: object = self.provider_account_id
        if acc_obj is None:
            object.__setattr__(self, "provider_account_id", "")
        elif not isinstance(acc_obj, str):
            object.__setattr__(self, "provider_account_id", str(acc_obj))

        status_obj: object = self.status
        if not isinstance(status_obj, WebhookEventStatus):
            if isinstance(status_obj, str):
                try:
                    object.__setattr__(self, "status", WebhookEventStatus(status_obj))
                except ValueError as exc:
                    raise InvalidPaymentError(
                        f"Invalid webhook event status: {status_obj}"
                    ) from exc
            else:
                raise InvalidPaymentError(f"Invalid webhook event status: {status_obj}")

        if self.attempt_count < 0:
            raise InvalidPaymentError("attempt_count must be non-negative")

        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise InvalidPaymentError("received_at must be timezone-aware (UTC)")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise InvalidPaymentError("updated_at must be timezone-aware (UTC)")

    def can_transition_to(self, new_status: WebhookEventStatus) -> bool:
        if new_status == self.status:
            return True
        allowed = _ALLOWED_WEBHOOK_EVENT_TRANSITIONS.get(self.status, frozenset())
        return new_status in allowed

    def mark_processing(self) -> ProviderWebhookEvent:
        if not self.can_transition_to(WebhookEventStatus.PROCESSING):
            raise InvalidPaymentError(
                f"Cannot transition webhook event from {self.status.value} to PROCESSING"
            )
        return ProviderWebhookEvent(
            id=self.id,
            provider=self.provider,
            event_id=self.event_id,
            status=WebhookEventStatus.PROCESSING,
            provider_account_id=self.provider_account_id,
            received_at=self.received_at,
            updated_at=datetime.now(UTC),
            attempt_count=self.attempt_count + 1,
            last_error_code=self.last_error_code,
            entity_reference=self.entity_reference,
            event_type=self.event_type,
            payload_hash=self.payload_hash,
            trace_id=self.trace_id,
        )

    def mark_processed(self, entity_reference: str | None = None) -> ProviderWebhookEvent:
        if not self.can_transition_to(WebhookEventStatus.PROCESSED):
            raise InvalidPaymentError(
                f"Cannot transition webhook event from {self.status.value} to PROCESSED"
            )
        return ProviderWebhookEvent(
            id=self.id,
            provider=self.provider,
            event_id=self.event_id,
            status=WebhookEventStatus.PROCESSED,
            provider_account_id=self.provider_account_id,
            received_at=self.received_at,
            updated_at=datetime.now(UTC),
            attempt_count=self.attempt_count,
            last_error_code=None,
            entity_reference=entity_reference or self.entity_reference,
            event_type=self.event_type,
            payload_hash=self.payload_hash,
            trace_id=self.trace_id,
        )

    def mark_failed(self, error_code: str) -> ProviderWebhookEvent:
        if not self.can_transition_to(WebhookEventStatus.FAILED):
            raise InvalidPaymentError(
                f"Cannot transition webhook event from {self.status.value} to FAILED"
            )
        return ProviderWebhookEvent(
            id=self.id,
            provider=self.provider,
            event_id=self.event_id,
            status=WebhookEventStatus.FAILED,
            provider_account_id=self.provider_account_id,
            received_at=self.received_at,
            updated_at=datetime.now(UTC),
            attempt_count=self.attempt_count,
            last_error_code=error_code,
            entity_reference=self.entity_reference,
            event_type=self.event_type,
            payload_hash=self.payload_hash,
            trace_id=self.trace_id,
        )

    def mark_quarantined(self, reason_code: str) -> ProviderWebhookEvent:
        if not self.can_transition_to(WebhookEventStatus.QUARANTINED):
            raise InvalidPaymentError(
                f"Cannot transition webhook event from {self.status.value} to QUARANTINED"
            )
        return ProviderWebhookEvent(
            id=self.id,
            provider=self.provider,
            event_id=self.event_id,
            status=WebhookEventStatus.QUARANTINED,
            provider_account_id=self.provider_account_id,
            received_at=self.received_at,
            updated_at=datetime.now(UTC),
            attempt_count=self.attempt_count,
            last_error_code=reason_code,
            entity_reference=self.entity_reference,
            event_type=self.event_type,
            payload_hash=self.payload_hash,
            trace_id=self.trace_id,
        )

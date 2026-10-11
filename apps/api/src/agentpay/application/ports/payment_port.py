"""Application ports for Payment execution, Order management, and Idempotency (§39, §41, §42).

Following Clean Architecture (§8, §10), every method strictly requires
an explicit `tenant_id` partition parameter (§49, §50).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentpay.domain.money import Money
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    PaymentAttempt,
    Refund,
)


@runtime_checkable
class PaymentProvider(Protocol):
    """Port for communicating with payment service providers (e.g. Razorpay, Mock) (§39)."""

    def create_payment(
        self,
        tenant_id: str,
        intent_id: str,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentAttempt:
        """Initiate payment with provider using an idempotency key."""
        ...

    def get_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Fetch current payment status from provider for reconciliation."""
        ...

    def capture_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Capture an authorized payment."""
        ...

    def refund(
        self,
        tenant_id: str,
        attempt_id: str,
        amount: Money,
        reason: str,
    ) -> Refund:
        """Process a refund for a payment attempt."""
        ...


@runtime_checkable
class PaymentRepository(Protocol):
    """Port for persisting PaymentAttempt history."""

    def get(self, tenant_id: str, attempt_id: str) -> PaymentAttempt | None:
        """Retrieve a payment attempt by ID."""
        ...

    def save(self, tenant_id: str, attempt: PaymentAttempt) -> None:
        """Persist or update a payment attempt."""
        ...


@runtime_checkable
class OrderRepository(Protocol):
    """Port for persisting commercial Order commitments."""

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        """Retrieve an order by ID."""
        ...

    def find_by_payment_attempt(self, tenant_id: str, attempt_id: str) -> Order | None:
        """Retrieve an order associated with a specific payment attempt ID (§41, B-REC-01)."""
        ...

    def save(self, tenant_id: str, order: Order) -> None:
        """Persist or update an order."""
        ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Port for tracking idempotency keys to prevent duplicate operations (§42)."""

    def get(self, tenant_id: str, key: str) -> IdempotencyRecord | None:
        """Retrieve previously recorded operation result for a key."""
        ...

    def save(self, tenant_id: str, record: IdempotencyRecord) -> None:
        """Store operation result associated with an idempotency key."""
        ...

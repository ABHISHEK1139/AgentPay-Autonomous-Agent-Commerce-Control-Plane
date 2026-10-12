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
    ProviderTransactionMapping,
    ProviderWebhookEvent,
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


@runtime_checkable
class ProviderTransactionMappingRepository(Protocol):
    """Port for persisting and resolving provider transaction mappings (§39, §41, §49)."""

    def save(self, mapping: ProviderTransactionMapping) -> None:
        """Persist a new provider transaction mapping."""
        ...

    def find_by_provider_transaction(
        self,
        provider: str,
        provider_transaction_id: str,
        provider_account_id: str | None = None,
    ) -> ProviderTransactionMapping | None:
        """Resolve internal tenant and attempt from an external provider transaction ID (payment_id, order_id, etc.)."""
        ...

    def find_by_attempt_id(
        self,
        tenant_id: str,
        attempt_id: str,
    ) -> ProviderTransactionMapping | None:
        """Retrieve mapping for a specific internal attempt ID within a tenant partition."""
        ...


@runtime_checkable
class ProviderWebhookEventRepository(Protocol):
    """Port for durable persistence and atomic deduplication of provider webhook events (§42, §61, AP-003)."""

    def get(self, event_pk: str) -> ProviderWebhookEvent | None:
        """Retrieve webhook event record by internal primary key."""
        ...

    def find_by_provider_event(
        self,
        provider: str,
        event_id: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        """Find an existing webhook event by provider, external event_id, and optional account."""
        ...

    def save(self, event: ProviderWebhookEvent) -> None:
        """Persist or update an existing webhook event record."""
        ...

    def claim_event(
        self,
        provider: str | ProviderWebhookEvent,
        provider_account_id: str = "",
        event_id: str = "",
        payload_hash: str | None = None,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[ProviderWebhookEvent, bool]:
        """Atomically claim a webhook event before state transitions.

        Returns:
            (event, True) if newly claimed and recorded.
            (existing_event, False) if event with same (provider, provider_account_id, event_id) was already recorded.
        """
        ...

    def mark_processed(
        self,
        provider: str,
        event_id: str,
        entity_reference: str | None = None,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        """Mark an existing event as PROCESSED."""
        ...

    def mark_failed(
        self,
        provider: str,
        event_id: str,
        error_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        """Mark an existing event as FAILED."""
        ...

    def mark_quarantined(
        self,
        provider: str,
        event_id: str,
        reason_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        """Mark an existing event as QUARANTINED."""
        ...

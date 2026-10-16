"""Thread-safe, tenant-partitioned in-memory repositories for Payments, Orders, and Idempotency (§39, §41, §42, §49)."""

from __future__ import annotations

import threading
import uuid

from agentpay.application.ports.payment_port import (
    IdempotencyStore,
    OrderRepository,
    PaymentRepository,
    ProviderTransactionMappingRepository,
    ProviderWebhookEventRepository,
    RefundRepository,
)
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    PaymentAttempt,
    ProviderTransactionMapping,
    ProviderWebhookEvent,
    Refund,
    WebhookEventStatus,
)


class InMemoryPaymentRepository(PaymentRepository):
    """Thread-safe in-memory store for PaymentAttempts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> attempt_id -> PaymentAttempt
        self._attempts: dict[str, dict[str, PaymentAttempt]] = {}

    def get(self, tenant_id: str, attempt_id: str) -> PaymentAttempt | None:
        with self._lock:
            return self._attempts.get(tenant_id, {}).get(attempt_id)

    def save(self, tenant_id: str, attempt: PaymentAttempt) -> None:
        if attempt.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"PaymentAttempt tenant '{attempt.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._attempts.setdefault(tenant_id, {})
            tenant_store[attempt.attempt_id] = attempt


class InMemoryRefundRepository(RefundRepository):
    """Thread-safe in-memory store for Refunds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> refund_id -> Refund
        self._refunds: dict[str, dict[str, Refund]] = {}

    def get(self, tenant_id: str, refund_id: str) -> Refund | None:
        with self._lock:
            return self._refunds.get(tenant_id, {}).get(refund_id)

    def save(self, tenant_id: str, refund: Refund) -> None:
        if refund.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Refund tenant '{refund.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._refunds.setdefault(tenant_id, {})
            tenant_store[refund.refund_id] = refund


class InMemoryOrderRepository(OrderRepository):
    """Thread-safe in-memory store for Orders."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> order_id -> Order
        self._orders: dict[str, dict[str, Order]] = {}

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(tenant_id, {}).get(order_id)

    def find_by_payment_attempt(self, tenant_id: str, attempt_id: str) -> Order | None:
        with self._lock:
            for candidate in self._orders.get(tenant_id, {}).values():
                if candidate.payment_attempt_id == attempt_id or candidate.order_id == attempt_id:
                    return candidate
            return None

    def save(self, tenant_id: str, order: Order) -> None:
        if order.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Order tenant '{order.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._orders.setdefault(tenant_id, {})
            tenant_store[order.order_id] = order


class InMemoryIdempotencyStore(IdempotencyStore):
    """Thread-safe in-memory store for IdempotencyRecords (§42)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> key -> IdempotencyRecord
        self._records: dict[str, dict[str, IdempotencyRecord]] = {}

    def get(self, tenant_id: str, key: str) -> IdempotencyRecord | None:
        with self._lock:
            return self._records.get(tenant_id, {}).get(key)

    def save(self, tenant_id: str, record: IdempotencyRecord) -> None:
        if record.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"IdempotencyRecord tenant '{record.tenant_id}' does not match context '{tenant_id}'"
            )
        with self._lock:
            tenant_store = self._records.setdefault(tenant_id, {})
            tenant_store[record.key] = record


class InMemoryProviderTransactionMappingRepository(ProviderTransactionMappingRepository):
    """Thread-safe in-memory store for ProviderTransactionMappings (§39, §41, §49)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (provider, provider_transaction_id, provider_account_id) -> ProviderTransactionMapping
        self._by_provider_tx: dict[tuple[str, str, str | None], ProviderTransactionMapping] = {}
        # (tenant_id, attempt_id) -> ProviderTransactionMapping
        self._by_attempt: dict[tuple[str, str], ProviderTransactionMapping] = {}

    def save(self, mapping: ProviderTransactionMapping) -> None:
        with self._lock:
            self._by_provider_tx[
                (mapping.provider, mapping.provider_payment_id, mapping.provider_account_id)
            ] = mapping
            if mapping.provider_account_id is not None:
                self._by_provider_tx.setdefault(
                    (mapping.provider, mapping.provider_payment_id, None), mapping
                )
            if mapping.provider_order_id:
                self._by_provider_tx[
                    (mapping.provider, mapping.provider_order_id, mapping.provider_account_id)
                ] = mapping
                if mapping.provider_account_id is not None:
                    self._by_provider_tx.setdefault(
                        (mapping.provider, mapping.provider_order_id, None), mapping
                    )
            self._by_attempt[(mapping.tenant_id, mapping.attempt_id)] = mapping

    def find_by_provider_transaction(
        self,
        provider: str,
        provider_transaction_id: str,
        provider_account_id: str | None = None,
    ) -> ProviderTransactionMapping | None:
        with self._lock:
            match = self._by_provider_tx.get(
                (provider, provider_transaction_id, provider_account_id)
            )
            if match is not None:
                return match
            if provider_account_id is not None:
                match = self._by_provider_tx.get((provider, provider_transaction_id, None))
                if match is not None:
                    return match
            for (p, tx_id, _), item in self._by_provider_tx.items():
                if (not provider or p == provider) and tx_id == provider_transaction_id:
                    return item
            return None

    def find_by_attempt_id(
        self,
        tenant_id: str,
        attempt_id: str,
    ) -> ProviderTransactionMapping | None:
        with self._lock:
            return self._by_attempt.get((tenant_id, attempt_id))


class InMemoryProviderWebhookEventRepository(ProviderWebhookEventRepository):
    """Thread-safe in-memory store for ProviderWebhookEvents (§42, §61, AP-003)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, ProviderWebhookEvent] = {}
        # (provider, provider_account_id, event_id) -> ProviderWebhookEvent
        self._by_provider_event: dict[tuple[str, str, str], ProviderWebhookEvent] = {}

    def get(self, event_pk: str) -> ProviderWebhookEvent | None:
        with self._lock:
            return self._by_id.get(event_pk)

    def find_by_provider_event(
        self,
        provider: str,
        event_id: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        acc_id = provider_account_id or ""
        with self._lock:
            match = self._by_provider_event.get((provider, acc_id, event_id))
            if match is not None:
                return match
            if not acc_id:
                for (p, _, e), event in self._by_provider_event.items():
                    if p == provider and e == event_id:
                        return event
            return None

    def save(self, event: ProviderWebhookEvent) -> None:
        with self._lock:
            acc_id = event.provider_account_id or ""
            self._by_id[event.id] = event
            self._by_provider_event[(event.provider, acc_id, event.event_id)] = event

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
        with self._lock:
            if isinstance(provider, ProviderWebhookEvent):
                ev = provider
                prov = ev.provider
                acc_id = ev.provider_account_id or ""
                evt_id = ev.event_id
            else:
                prov = provider
                acc_id = provider_account_id or ""
                evt_id = event_id
                ev = ProviderWebhookEvent(
                    id=f"pwe_{uuid.uuid4().hex[:12]}",
                    provider=prov,
                    provider_account_id=acc_id,
                    event_id=evt_id,
                    status=WebhookEventStatus.PROCESSING,
                    payload_hash=payload_hash,
                    event_type=event_type,
                    trace_id=trace_id,
                )

            key = (prov, acc_id, evt_id)
            existing = self._by_provider_event.get(key)
            if existing is not None:
                return existing, False

            if not acc_id:
                for (p, _, e), candidate in self._by_provider_event.items():
                    if p == prov and e == evt_id:
                        return candidate, False

            self._by_id[ev.id] = ev
            self._by_provider_event[key] = ev
            return ev, True

    def mark_processed(
        self,
        provider: str,
        event_id: str,
        entity_reference: str | None = None,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        with self._lock:
            event = self.find_by_provider_event(provider, event_id, provider_account_id)
            if event is None:
                return None
            updated = event.mark_processed(entity_reference)
            self._by_id[updated.id] = updated
            acc_id = updated.provider_account_id or ""
            self._by_provider_event[(updated.provider, acc_id, updated.event_id)] = updated
            return updated

    def mark_failed(
        self,
        provider: str,
        event_id: str,
        error_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        with self._lock:
            event = self.find_by_provider_event(provider, event_id, provider_account_id)
            if event is None:
                return None
            updated = event.mark_failed(error_code)
            self._by_id[updated.id] = updated
            acc_id = updated.provider_account_id or ""
            self._by_provider_event[(updated.provider, acc_id, updated.event_id)] = updated
            return updated

    def mark_quarantined(
        self,
        provider: str,
        event_id: str,
        reason_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        with self._lock:
            event = self.find_by_provider_event(provider, event_id, provider_account_id)
            if event is None:
                return None
            updated = event.mark_quarantined(reason_code)
            self._by_id[updated.id] = updated
            acc_id = updated.provider_account_id or ""
            self._by_provider_event[(updated.provider, acc_id, updated.event_id)] = updated
            return updated

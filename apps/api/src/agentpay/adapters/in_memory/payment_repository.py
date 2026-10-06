"""Thread-safe, tenant-partitioned in-memory repositories for Payments, Orders, and Idempotency (§39, §41, §42, §49)."""

from __future__ import annotations

import threading

from agentpay.application.ports.payment_port import (
    IdempotencyStore,
    OrderRepository,
    PaymentRepository,
)
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    PaymentAttempt,
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


class InMemoryOrderRepository(OrderRepository):
    """Thread-safe in-memory store for Orders."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tenant_id -> order_id -> Order
        self._orders: dict[str, dict[str, Order]] = {}

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(tenant_id, {}).get(order_id)

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

"""Thread-safe in-memory transactional outbox repository adapter (§106)."""

from __future__ import annotations

import threading

from agentpay.domain.outbox import OutboxEvent, OutboxStatus


class InMemoryOutboxRepository:
    """Thread-safe, tenant-partitioned in-memory outbox repository."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Key: (tenant_id, event_id) -> OutboxEvent
        self._events: dict[tuple[str, str], OutboxEvent] = {}

    def save(self, event: OutboxEvent) -> None:
        """Saves or updates an outbox event."""
        with self._lock:
            self._events[(event.tenant_id, event.event_id)] = event

    def get(self, tenant_id: str, event_id: str) -> OutboxEvent | None:
        """Retrieves an outbox event by tenant_id and event_id."""
        with self._lock:
            return self._events.get((tenant_id, event_id))

    def list_pending(self, tenant_id: str | None = None, limit: int = 100) -> list[OutboxEvent]:
        """Lists pending outbox events eligible for dispatch, ordered by created_at."""
        with self._lock:
            events = [
                evt
                for evt in self._events.values()
                if evt.status == OutboxStatus.PENDING
                and (tenant_id is None or evt.tenant_id == tenant_id)
            ]
            events.sort(key=lambda e: e.created_at)
            return events[:limit]

    def mark_dispatched(self, tenant_id: str, event_id: str) -> None:
        """Marks an event as DISPATCHED."""
        with self._lock:
            event = self._events.get((tenant_id, event_id))
            if event:
                event.mark_dispatched()

    def record_failure(self, tenant_id: str, event_id: str, error: str) -> None:
        """Records failure and updates event status."""
        with self._lock:
            event = self._events.get((tenant_id, event_id))
            if event:
                event.record_failure(error)

    def count(self, tenant_id: str | None = None, status: OutboxStatus | None = None) -> int:
        """Returns count of events matching criteria."""
        with self._lock:
            return sum(
                1
                for evt in self._events.values()
                if (tenant_id is None or evt.tenant_id == tenant_id)
                and (status is None or evt.status == status)
            )

    def clear(self) -> None:
        """Clears all events (useful for hermetic test teardown)."""
        with self._lock:
            self._events.clear()

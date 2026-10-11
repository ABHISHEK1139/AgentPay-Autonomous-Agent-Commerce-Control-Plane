"""Thread-safe in-memory transactional outbox repository adapter (§106)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from agentpay.domain.outbox import OutboxEvent, OutboxStatus


class InMemoryOutboxRepository:
    """Thread-safe, tenant-partitioned in-memory outbox repository (§106, B-OUT-01)."""

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

    def claim_pending(
        self,
        worker_id: str,
        lease_seconds: int = 30,
        tenant_id: str | None = None,
        limit: int = 50,
        at: datetime | None = None,
    ) -> list[OutboxEvent]:
        """Atomically claims eligible pending/stale outbox events under a worker lease (§106, B-OUT-01)."""
        now = at or datetime.now(UTC)
        with self._lock:
            eligible: list[OutboxEvent] = []
            for evt in self._events.values():
                if tenant_id is not None and evt.tenant_id != tenant_id:
                    continue
                if evt.is_claimable(now):
                    eligible.append(evt)

            eligible.sort(key=lambda e: e.created_at)
            claimed = eligible[:limit]
            for evt in claimed:
                evt.claim(worker_id=worker_id, lease_seconds=lease_seconds, at=now)
            return claimed

    def list_pending(self, tenant_id: str | None = None, limit: int = 100) -> list[OutboxEvent]:
        """Lists pending outbox events eligible for dispatch, ordered by created_at."""
        with self._lock:
            events = [
                evt
                for evt in self._events.values()
                if evt.is_claimable() and (tenant_id is None or evt.tenant_id == tenant_id)
            ]
            events.sort(key=lambda e: e.created_at)
            return events[:limit]

    def mark_dispatched(self, tenant_id: str, event_id: str) -> None:
        """Marks an event as DISPATCHED and releases lease."""
        with self._lock:
            event = self._events.get((tenant_id, event_id))
            if event:
                event.mark_dispatched()

    def record_failure(
        self,
        tenant_id: str,
        event_id: str,
        error: str,
        backoff_base_seconds: float = 2.0,
        at: datetime | None = None,
    ) -> None:
        """Records failure, computes exponential backoff, and updates event status."""
        with self._lock:
            event = self._events.get((tenant_id, event_id))
            if event:
                event.record_failure(error, backoff_base_seconds=backoff_base_seconds, at=at)

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

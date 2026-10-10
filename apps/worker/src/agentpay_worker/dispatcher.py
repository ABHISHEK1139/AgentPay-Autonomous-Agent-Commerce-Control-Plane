"""Transactional Outbox Dispatcher (§106)."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from agentpay.application.services.event_bus import EventBus
from agentpay.domain.outbox import OutboxEvent

logger = logging.getLogger(__name__)


class OutboxRepositoryProtocol(Protocol):
    """Protocol for OutboxRepository needed by dispatcher."""

    def list_pending(self, tenant_id: str | None = None, limit: int = 100) -> list[OutboxEvent]: ...

    def mark_dispatched(self, tenant_id: str, event_id: str) -> None: ...

    def record_failure(self, tenant_id: str, event_id: str, error: str) -> None: ...


class OutboxDispatcher:
    """Dispatches pending outbox events to the event bus with at-least-once delivery (§106)."""

    def __init__(self, outbox_repo: Any, event_bus: EventBus) -> None:
        self.outbox_repo = outbox_repo
        self.event_bus = event_bus

    def dispatch_batch(self, limit: int = 50) -> tuple[int, int]:
        """Dispatches a batch of pending events.

        Returns (dispatched_count, failed_count).
        """
        pending_events: list[OutboxEvent] = self.outbox_repo.list_pending(limit=limit)
        dispatched = 0
        failed = 0

        for event in pending_events:
            try:
                self.event_bus.publish(event)
                self.outbox_repo.mark_dispatched(event.tenant_id, event.event_id)
                dispatched += 1
            except Exception as exc:
                logger.error("Failed to dispatch outbox event %s: %s", event.event_id, exc)
                self.outbox_repo.record_failure(event.tenant_id, event.event_id, str(exc))
                failed += 1

        return (dispatched, failed)

"""In-process asynchronous event bus implementation (§105)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable

from agentpay.domain.outbox import OutboxEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[OutboxEvent], None]


class EventBus:
    """Thread-safe in-process event bus for publishing domain outbox events (§105)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._processed_events: set[str] = set()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribes an event handler to a specific event type or wildcard '*'."""
        with self._lock:
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribes a handler."""
        with self._lock:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)

    def publish(self, event: OutboxEvent, allow_duplicate: bool = False) -> int:
        """Publishes an outbox event to all matching subscribers.

        Returns the number of handlers successfully executed.
        Guarantees handler error isolation: one failing handler does not abort others.
        """
        with self._lock:
            if not allow_duplicate and event.event_id in self._processed_events:
                # Idempotent no-op for already processed event delivery
                return 0

            # Gather subscribers: specific event type + wildcard '*'
            handlers = list(self._subscribers.get(event.event_type, []))
            handlers.extend(self._subscribers.get("*", []))

        executed = 0
        errors: list[Exception] = []

        for handler in handlers:
            try:
                handler(event)
                executed += 1
            except Exception as exc:
                logger.error(
                    "Error executing handler %s for event %s: %s",
                    getattr(handler, "__name__", str(handler)),
                    event.event_id,
                    exc,
                )
                errors.append(exc)

        if errors:
            raise RuntimeError(f"EventBus handler errors ({len(errors)}): {errors[0]}") from errors[
                0
            ]

        with self._lock:
            self._processed_events.add(event.event_id)

        return executed

    def clear(self) -> None:
        """Clears subscribers and deduplication cache."""
        with self._lock:
            self._subscribers.clear()
            self._processed_events.clear()

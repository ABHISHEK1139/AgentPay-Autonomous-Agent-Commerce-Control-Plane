"""Transactional Outbox domain models (§106).

Enforces domain layer purity (§10): standard library only, zero framework imports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class OutboxStatus(StrEnum):
    """Lifecycle states for an outbox event."""

    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"


@dataclass
class OutboxEvent:
    """Canonical transactional outbox event record.

    Ensures domain state changes and external/asynchronous notifications
    are staged atomically in the same transactional unit of work (§106).
    """

    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    status: OutboxStatus = OutboxStatus.PENDING
    retry_count: int = 0
    max_retries: int = 5
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    dispatched_at: datetime | None = None
    error_message: str | None = None

    def mark_dispatched(self, at: datetime | None = None) -> None:
        """Transitions outbox event to DISPATCHED."""
        self.status = OutboxStatus.DISPATCHED
        self.dispatched_at = at or datetime.now(UTC)
        self.error_message = None

    def record_failure(self, error: str) -> None:
        """Records an execution failure and increments retry counter."""
        self.retry_count += 1
        self.error_message = error
        if self.retry_count >= self.max_retries:
            self.status = OutboxStatus.FAILED
        else:
            self.status = OutboxStatus.PENDING

    @property
    def can_retry(self) -> bool:
        """Returns True if the event has remaining retry attempts."""
        return self.retry_count < self.max_retries

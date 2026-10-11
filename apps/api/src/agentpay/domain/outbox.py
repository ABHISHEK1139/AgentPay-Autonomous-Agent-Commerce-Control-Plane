"""Transactional Outbox domain models (§106).

Enforces domain layer purity (§10): standard library only, zero framework imports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class OutboxStatus(StrEnum):
    """Lifecycle states for an outbox event (§106)."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"


@dataclass
class OutboxEvent:
    """Canonical transactional outbox event record.

    Ensures domain state changes and external/asynchronous notifications
    are staged atomically in the same transactional unit of work (§106).
    Supports distributed worker leasing and exponential backoff retry (§105, B-OUT-01).
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
    locked_by: str | None = None
    locked_until: datetime | None = None
    next_retry_at: datetime | None = None

    def claim(self, worker_id: str, lease_seconds: int = 30, at: datetime | None = None) -> None:
        """Claims event under a distributed worker lease (§106, B-OUT-01)."""
        now = at or datetime.now(UTC)
        self.status = OutboxStatus.PROCESSING
        self.locked_by = worker_id
        self.locked_until = now + timedelta(seconds=lease_seconds)

    def mark_dispatched(self, at: datetime | None = None) -> None:
        """Transitions outbox event to DISPATCHED and releases lease."""
        self.status = OutboxStatus.DISPATCHED
        self.dispatched_at = at or datetime.now(UTC)
        self.error_message = None
        self.locked_by = None
        self.locked_until = None

    def record_failure(
        self,
        error: str,
        backoff_base_seconds: float = 2.0,
        at: datetime | None = None,
    ) -> None:
        """Records an execution failure, calculates exponential backoff, and releases lease."""
        now = at or datetime.now(UTC)
        self.retry_count += 1
        self.error_message = error
        self.locked_by = None
        self.locked_until = None
        if self.retry_count >= self.max_retries:
            self.status = OutboxStatus.FAILED
            self.next_retry_at = None
        else:
            self.status = OutboxStatus.PENDING
            delay_sec = (2.0 ** (self.retry_count - 1)) * backoff_base_seconds
            self.next_retry_at = now + timedelta(seconds=delay_sec)

    def is_claimable(self, at: datetime | None = None) -> bool:
        """Returns True if the event can be acquired by a worker."""
        now = at or datetime.now(UTC)
        if self.status == OutboxStatus.PENDING:
            return self.next_retry_at is None or self.next_retry_at <= now
        if self.status == OutboxStatus.PROCESSING:
            # Lease expired from stalled or crashed worker
            return self.locked_until is not None and self.locked_until < now
        return False

    @property
    def can_retry(self) -> bool:
        """Returns True if the event has remaining retry attempts."""
        return self.retry_count < self.max_retries

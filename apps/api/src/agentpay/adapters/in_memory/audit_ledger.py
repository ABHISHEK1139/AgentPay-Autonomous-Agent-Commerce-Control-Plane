"""In-memory implementation of the AuditLedger port (§45, §46).

Provides hermetic, tamper-evident hash chaining and sensitive field redaction
for testing and local execution without external databases or services.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentpay.application.ports.audit_port import AuditLedger
from agentpay.domain.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    AuditHasher,
    SensitiveFieldRedactor,
)
from agentpay.domain.errors import InvalidAuditEventError


class InMemoryAuditLedger(AuditLedger):
    """Thread-safe, tenant-partitioned in-memory audit ledger."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenant_events: dict[str, list[AuditEvent]] = {}

    def record_event(
        self,
        event_type: AuditEventType | str,
        action_id: str | None,
        tenant_id: str,
        actor: str,
        data: dict[str, Any],
        agent_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuditEvent:
        tenant_id_val: object = tenant_id
        if not isinstance(tenant_id_val, str) or not tenant_id_val.strip():
            raise InvalidAuditEventError("tenant_id must be a non-empty string")
        actor_val: object = actor
        if not isinstance(actor_val, str) or not actor_val.strip():
            raise InvalidAuditEventError("actor must be a non-empty string")

        resolved_type = AuditEventType(event_type)

        redacted_data = SensitiveFieldRedactor.redact(data)
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)

        with self._lock:
            events = self._tenant_events.setdefault(tenant_id, [])
            previous_hash = events[-1].event_hash if events else GENESIS_HASH
            event_hash = AuditHasher.compute_event_hash(
                event_data=redacted_data,
                previous_hash=previous_hash,
                actor=actor,
                event_type=resolved_type.value,
                timestamp=now,
                tenant_id=tenant_id,
                agent_id=agent_id,
                action_id=action_id,
                trace_id=trace_id,
            )

            event = AuditEvent(
                event_id=event_id,
                tenant_id=tenant_id,
                actor=actor,
                event_type=resolved_type,
                timestamp=now,
                data=redacted_data,
                previous_event_hash=previous_hash,
                event_hash=event_hash,
                agent_id=agent_id,
                action_id=action_id,
                request_id=request_id,
                correlation_id=correlation_id,
                trace_id=trace_id,
                version=1,
            )
            events.append(event)
            return event

    def get_events(
        self,
        tenant_id: str,
        action_id: str | None = None,
    ) -> list[AuditEvent]:
        with self._lock:
            events = self._tenant_events.get(tenant_id, [])
            if action_id is None:
                return list(events)
            return [e for e in events if e.action_id == action_id]

    def verify_chain(self, tenant_id: str) -> bool:
        with self._lock:
            events = self._tenant_events.get(tenant_id, [])
            if not events:
                return True

            for idx, event in enumerate(events):
                expected_prev = GENESIS_HASH if idx == 0 else events[idx - 1].event_hash
                if event.previous_event_hash != expected_prev:
                    return False

                recomputed = AuditHasher.compute_event_hash(
                    event_data=event.data,
                    previous_hash=event.previous_event_hash,
                    actor=event.actor,
                    event_type=event.event_type.value,
                    timestamp=event.timestamp,
                    tenant_id=event.tenant_id,
                    agent_id=event.agent_id,
                    action_id=event.action_id,
                    trace_id=event.trace_id,
                )
                if event.event_hash != recomputed:
                    return False

            return True

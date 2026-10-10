"""Application port for the immutable audit ledger.

Following Clean Architecture (§8, §10), defines an abstract protocol
for recording and retrieving tamper-evident audit events.
Every method strictly requires an explicit tenant partition parameter (§49, §50).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentpay.domain.audit import AuditEvent, AuditEventType


@runtime_checkable
class AuditLedger(Protocol):
    """Port for recording and querying tamper-evident audit records."""

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
        """Record an immutable, tamper-evident audit event."""
        ...

    def get_events(
        self,
        tenant_id: str,
        action_id: str | None = None,
    ) -> list[AuditEvent]:
        """Retrieve audit events for a tenant, optionally filtered by action_id."""
        ...

    def verify_chain(self, tenant_id: str) -> bool:
        """Verify the cryptographic hash-chain integrity of a tenant's audit trail."""
        ...

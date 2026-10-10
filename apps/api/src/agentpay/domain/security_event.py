"""Security event domain models (§62, §63).

Enforces domain layer purity (§10): standard library only, zero framework imports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SecurityEventType(StrEnum):
    """Categorization of security anomalies and abuse vectors (§62, §63)."""

    CREDENTIAL_STUFFING = "CREDENTIAL_STUFFING"
    SCRAPING_BURST = "SCRAPING_BURST"
    TOOL_LOOP_EXHAUSTION = "TOOL_LOOP_EXHAUSTION"
    SPEND_VELOCITY_SPIKE = "SPEND_VELOCITY_SPIKE"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    UNAUTHORIZED_OPERATION = "UNAUTHORIZED_OPERATION"
    REPLAY_ATTACK = "REPLAY_ATTACK"
    CROSS_TENANT_VIOLATION = "CROSS_TENANT_VIOLATION"


class SecuritySeverity(StrEnum):
    """Risk severity levels for security events."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityActionTaken(StrEnum):
    """Mitigation action taken in response to a security incident."""

    LOG_ONLY = "LOG_ONLY"
    RATE_LIMIT = "RATE_LIMIT"
    QUARANTINE_AGENT = "QUARANTINE_AGENT"
    SUSPEND_AGENT = "SUSPEND_AGENT"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class SecurityEvent:
    """Tamper-evident security incident record (§62, §63).

    Emitted upon detection of malicious patterns or abuse thresholds.
    """

    tenant_id: str
    event_type: SecurityEventType
    severity: SecuritySeverity
    action_taken: SecurityActionTaken
    description: str
    event_id: str = field(default_factory=lambda: f"sec_{uuid.uuid4().hex[:16]}")
    agent_id: str | None = None
    principal_id: str | None = None
    client_ip: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_audit_payload(self) -> dict[str, Any]:
        """Converts security event into an audit event metadata payload."""
        return {
            "security_event_id": self.event_id,
            "security_event_type": self.event_type.value,
            "severity": self.severity.value,
            "action_taken": self.action_taken.value,
            "description": self.description,
            "agent_id": self.agent_id,
            "principal_id": self.principal_id,
            "client_ip": self.client_ip,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }

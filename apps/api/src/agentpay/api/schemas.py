"""Standardized API schemas and error envelopes (§13, §38).

Following the API standard (§13):
- Structured error envelopes with code, message, and request_id.
- Explicit request and response payloads.
- Strict typing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ApiErrorDetail:
    """Standardized error detail envelope (§13)."""

    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApiErrorResponse:
    """Standardized root error envelope (§13)."""

    error: ApiErrorDetail

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActionSubmissionRequest:
    """Request payload for submitting an agent ActionRequest (§6, §38)."""

    action_id: str
    principal_id: str
    agent_id: str
    domain: str
    action: str
    resource_type: str
    resource_id: str
    amount_minor: int
    currency: str
    intent_purpose: str
    variant_id: str
    quantity: int = 1
    idempotency_key: str | None = None
    authorization_mode: str = "AUTONOMOUS"
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDecisionRequest:
    """Request payload for granting or rejecting an approval (§20, §66)."""

    approver_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDetailResponse:
    """Detailed response exposing all 8 binding fields and full lifecycle metadata (§20, §66)."""

    approval_id: str
    tenant_id: str
    principal_id: str
    action_id: str
    status: str
    amount_minor: int
    currency: str
    merchant_id: str
    resource_id: str
    action_version: int
    requested_at: str
    approver_id: str | None = None
    decided_at: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class EmergencyControlRequest:
    """Request payload for invoking emergency pause / kill switch (§21, §67)."""

    operator_id: str
    reason: str
    scope: str = "ALL_AUTONOMOUS_PAYMENTS"

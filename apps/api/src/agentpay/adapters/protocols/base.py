"""Base interfaces and error definitions for protocol adapters (§107, §108).

Provides:
- Abstract BaseProtocolAdapter mapping external agent protocol payloads into
  canonical ActionRequests and serializing execution results.
- Comprehensive ProtocolError hierarchy.
- Standard error envelope formatting adhering to §13.
- ISO 8601 UTC timestamp parser.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from agentpay.application.use_cases.purchase_flow import PurchaseExecutionResult
from agentpay.domain.action_request import ActionRequest


class ProtocolError(Exception):
    """Base class for all protocol adapter errors."""

    code: str = "PROTOCOL_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class MalformedPayloadError(ProtocolError):
    """Raised when an incoming protocol payload is missing required fields or has invalid types."""

    code: str = "MALFORMED_PAYLOAD"


class ProtocolTranslationError(ProtocolError):
    """Raised when an incoming protocol payload cannot be translated into canonical models."""

    code: str = "TRANSLATION_ERROR"


class UnsupportedProtocolVersionError(ProtocolError):
    """Raised when a protocol version is unrecognized or unsupported."""

    code: str = "UNSUPPORTED_PROTOCOL_VERSION"


class UnsupportedActionError(ProtocolError):
    """Raised when a requested action or capability cannot be mapped to AgentPay."""

    code: str = "UNSUPPORTED_ACTION"


def parse_iso_utc(timestamp_str: str | None) -> datetime:
    """Parse ISO8601 string into guaranteed timezone-aware UTC datetime."""
    if not timestamp_str:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception as err:
        raise MalformedPayloadError(f"Invalid timestamp format '{timestamp_str}': {err}") from err


def format_error_envelope(
    code: str,
    message: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format standardized error envelope according to §13."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or f"req_{uuid.uuid4().hex[:12]}",
            "details": details or {},
        }
    }


class BaseProtocolAdapter[TReq, TResp](ABC):
    """Abstract base class for all AgentPay protocol adapters (§107)."""

    @abstractmethod
    def parse_payload(self, raw_data: dict[str, Any]) -> TReq:
        """Parse raw dictionary into typed protocol request DTO."""
        ...

    @abstractmethod
    def to_action_request(
        self, request: TReq, tenant_id: str | None = None
    ) -> tuple[ActionRequest, dict[str, Any]]:
        """Translate typed protocol request into canonical ActionRequest and execution metadata."""
        ...

    @abstractmethod
    def from_execution_result(self, request: TReq, result: PurchaseExecutionResult) -> TResp:
        """Translate canonical execution result back into protocol response DTO."""
        ...

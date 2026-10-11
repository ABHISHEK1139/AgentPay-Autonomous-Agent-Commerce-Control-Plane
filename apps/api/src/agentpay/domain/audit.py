"""Pure domain models for the immutable audit ledger and traceability (§45, §46).

Following Clean Architecture (§8, §10):
- Pure Python standard library and internal domain concepts only.
- Zero imports of external web frameworks, ORMs, or third-party libraries.
- Tamper-evident hash chaining for audit integrity.
- Sensitive field redaction to minimize payment data exposure (§40).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from agentpay.domain.errors import InvalidAuditEventError


class AuditEventType(StrEnum):
    """Canonical audit event types for the AgentPay lifecycle (§45)."""

    AGENT_AUTHENTICATED = "AGENT_AUTHENTICATED"
    ACTION_REQUESTED = "ACTION_REQUESTED"
    INTENT_PARSED = "INTENT_PARSED"
    PRODUCT_SEARCHED = "PRODUCT_SEARCHED"
    EVIDENCE_RETRIEVED = "EVIDENCE_RETRIEVED"
    RECOMMENDATION_CREATED = "RECOMMENDATION_CREATED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    JEV_EVALUATED = "JEV_EVALUATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    AGENT_REVOKED = "AGENT_REVOKED"
    EMERGENCY_PAUSE = "EMERGENCY_PAUSE"
    SECURITY_ALERT = "SECURITY_ALERT"


GENESIS_HASH: Final[str] = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, tamper-evident audit record for a single lifecycle transition (§45).

    The event_hash chains to previous_event_hash to form a tamper-evident sequence.
    The data field stores auditable decision summaries and evidence references,
    NOT private chain-of-thought (§29).
    """

    event_id: str
    tenant_id: str
    actor: str
    event_type: AuditEventType
    timestamp: datetime
    data: dict[str, Any]
    previous_event_hash: str
    event_hash: str
    agent_id: str | None = None
    action_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        required_strings: tuple[tuple[str, object], ...] = (
            ("event_id", self.event_id),
            ("tenant_id", self.tenant_id),
            ("actor", self.actor),
        )
        for field_name, value in required_strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidAuditEventError(f"{field_name} must be a non-empty string")

        optional_strings: tuple[tuple[str, object], ...] = (
            ("agent_id", self.agent_id),
            ("action_id", self.action_id),
            ("request_id", self.request_id),
            ("correlation_id", self.correlation_id),
            ("trace_id", self.trace_id),
        )
        for field_name, value in optional_strings:
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise InvalidAuditEventError(f"{field_name} must be None or a non-empty string")

        event_type_val: object = self.event_type
        if isinstance(event_type_val, str) and not isinstance(event_type_val, AuditEventType):
            try:
                object.__setattr__(self, "event_type", AuditEventType(event_type_val))
            except ValueError as err:
                raise InvalidAuditEventError(
                    f"event_type must be an AuditEventType, got {event_type_val!r}"
                ) from err
        elif not isinstance(event_type_val, (str, AuditEventType)):
            raise InvalidAuditEventError(
                f"event_type must be an AuditEventType, got {event_type_val!r}"
            )

        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise InvalidAuditEventError("timestamp must be timezone-aware (UTC)")

        if not isinstance(self.data, dict):
            raise InvalidAuditEventError(f"data must be a dict, got {type(self.data).__name__}")

        hash_strings: tuple[tuple[str, object], ...] = (
            ("previous_event_hash", self.previous_event_hash),
            ("event_hash", self.event_hash),
        )
        for field_name, value in hash_strings:
            if not isinstance(value, str) or not value.strip():
                raise InvalidAuditEventError(f"{field_name} must be a non-empty string")

        version_val: object = self.version
        if isinstance(version_val, bool) or not isinstance(version_val, int) or version_val < 1:
            raise InvalidAuditEventError(f"version must be a positive int, got {version_val!r}")


class AuditHasher:
    """Deterministic hashing for tamper-evident audit chains (§46).

    Hash chain formula: SHA256(canonical_json(data) + previous_hash).
    """

    @staticmethod
    def canonical_json(data: dict[str, Any]) -> str:
        """Produce deterministic JSON: sorted keys, no extra whitespace, ASCII-safe."""
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def compute_hash(canonical_data: str, previous_hash: str) -> str:
        """Compute SHA-256 of (canonical_data + previous_hash)."""
        payload = canonical_data + previous_hash
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_event_hash(
        event_data: dict[str, Any],
        previous_hash: str,
        actor: str = "",
        event_type: str = "",
        timestamp: datetime | str = "",
        tenant_id: str = "",
        agent_id: str | None = None,
        action_id: str | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Convenience: canonical JSON of event metadata and data, then hash in one call (§29, §45)."""
        preimage_dict: dict[str, Any] = {
            "action_id": action_id or "",
            "actor": actor,
            "agent_id": agent_id or "",
            "data": event_data,
            "event_type": str(event_type),
            "tenant_id": tenant_id,
            "timestamp": timestamp.isoformat()
            if isinstance(timestamp, datetime)
            else str(timestamp),
            "trace_id": trace_id or "",
        }
        canonical = AuditHasher.canonical_json(preimage_dict)
        return AuditHasher.compute_hash(canonical, previous_hash)


_DEFAULT_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "card_number",
        "cardnumber",
        "card-number",
        "pan",
        "cvv",
        "cvc",
        "cid",
        "cvn",
        "security_code",
        "password",
        "secret",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "expiry",
        "expiration",
        "exp_month",
        "exp_year",
        "expiration_date",
        "authorization_code",
        "fingerprint",
    }
)

_NORMALIZED_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "cardnumber",
        "cardnum",
        "pan",
        "cvv",
        "cvc",
        "cid",
        "cvn",
        "securitycode",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "token",
        "accesstoken",
        "refreshtoken",
        "authtoken",
        "bearertoken",
        "apikey",
        "expiry",
        "expiration",
        "expmonth",
        "expyear",
        "expirationdate",
        "expdate",
        "authorizationcode",
        "authcode",
        "fingerprint",
    }
)


class SensitiveFieldRedactor:
    """Redacts sensitive fields from audit data to minimize payment data exposure (§40, §69)."""

    REDACTED: Final = "[REDACTED]"

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.lower().replace("-", "").replace("_", "").replace(" ", "")

    @staticmethod
    def redact(
        data: dict[str, Any],
        sensitive_keys: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Return a new dict with sensitive key values replaced by '[REDACTED]'.

        Handles nested dicts and lists recursively. Does not mutate the input.
        Matches keys case-insensitively and across delimiter variants (hyphen, underscore, space).
        """
        raw_keys = sensitive_keys if sensitive_keys is not None else _DEFAULT_SENSITIVE_KEYS
        norm_keys = frozenset(SensitiveFieldRedactor._normalize_key(k) for k in raw_keys)
        if sensitive_keys is None:
            norm_keys = norm_keys | _NORMALIZED_SENSITIVE_KEYS
        return SensitiveFieldRedactor._redact_recursive(data, raw_keys, norm_keys)

    @staticmethod
    def _redact_recursive(
        data: dict[str, Any],
        sensitive_keys: frozenset[str],
        normalized_keys: frozenset[str],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            norm_k = SensitiveFieldRedactor._normalize_key(str(key))
            if norm_k in normalized_keys or key in sensitive_keys:
                result[key] = SensitiveFieldRedactor.REDACTED
            elif isinstance(value, dict):
                result[key] = SensitiveFieldRedactor._redact_recursive(
                    value, sensitive_keys, normalized_keys
                )
            elif isinstance(value, list):
                result[key] = SensitiveFieldRedactor._redact_list(
                    value, sensitive_keys, normalized_keys
                )
            else:
                result[key] = value
        return result

    @staticmethod
    def _redact_list(
        items: list[Any],
        sensitive_keys: frozenset[str],
        normalized_keys: frozenset[str],
    ) -> list[Any]:
        result: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                result.append(
                    SensitiveFieldRedactor._redact_recursive(item, sensitive_keys, normalized_keys)
                )
            elif isinstance(item, list):
                result.append(
                    SensitiveFieldRedactor._redact_list(item, sensitive_keys, normalized_keys)
                )
            else:
                result.append(item)
        return result

"""AgentPay canonical JSON schemas and schema utilities (§13, §24, §45)."""

from __future__ import annotations

from typing import Any

ACTION_REQUEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ActionRequest",
    "type": "object",
    "required": [
        "action_id",
        "tenant_id",
        "principal_id",
        "agent_id",
        "domain",
        "action",
        "resource",
        "amount",
        "intent",
    ],
    "properties": {
        "action_id": {"type": "string"},
        "tenant_id": {"type": "string"},
        "principal_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "domain": {"type": "string", "enum": ["commerce", "procurement", "cloud", "saas"]},
        "action": {"type": "string"},
        "resource": {
            "type": "object",
            "required": ["type", "id"],
            "properties": {
                "type": {"type": "string"},
                "id": {"type": "string"},
            },
        },
        "amount": {
            "type": "object",
            "required": ["minor_units", "currency"],
            "properties": {
                "minor_units": {"type": "integer", "minimum": 0},
                "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            },
        },
        "intent": {
            "type": "object",
            "required": ["purpose"],
            "properties": {
                "purpose": {"type": "string"},
                "budget_max_minor_units": {"type": "integer", "minimum": 0},
            },
        },
        "authorization": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["AUTONOMOUS", "APPROVAL_REQUIRED"]},
            },
        },
    },
}

API_ERROR_ENVELOPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ApiErrorEnvelope",
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "trace_id": {"type": ["string", "null"]},
                "details": {"type": "object"},
            },
        },
    },
}

__all__ = [
    "ACTION_REQUEST_SCHEMA",
    "API_ERROR_ENVELOPE_SCHEMA",
]

"""Tests for Audit domain models, hashing, redaction, and in-memory ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.domain.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    AuditHasher,
    SensitiveFieldRedactor,
)
from agentpay.domain.errors import InvalidAuditEventError


def test_audit_event_creation_valid() -> None:
    now = datetime.now(UTC)
    event = AuditEvent(
        event_id="evt_01",
        tenant_id="tenant_alpha",
        actor="user_admin",
        event_type=AuditEventType.ACTION_REQUESTED,
        timestamp=now,
        data={"action": "purchase", "amount": 1000},
        previous_event_hash=GENESIS_HASH,
        event_hash="abc123hash",
        agent_id="agent_01",
        action_id="act_01",
    )
    assert event.event_id == "evt_01"
    assert event.tenant_id == "tenant_alpha"
    assert event.actor == "user_admin"
    assert event.event_type == AuditEventType.ACTION_REQUESTED
    assert event.data["action"] == "purchase"


def test_audit_event_validation_empty_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidAuditEventError, match="event_id"):
        AuditEvent(
            event_id="",
            tenant_id="t1",
            actor="u1",
            event_type=AuditEventType.ACTION_REQUESTED,
            timestamp=now,
            data={},
            previous_event_hash=GENESIS_HASH,
            event_hash="hash",
        )

    with pytest.raises(InvalidAuditEventError, match="tenant_id"):
        AuditEvent(
            event_id="e1",
            tenant_id="",
            actor="u1",
            event_type=AuditEventType.ACTION_REQUESTED,
            timestamp=now,
            data={},
            previous_event_hash=GENESIS_HASH,
            event_hash="hash",
        )

    with pytest.raises(InvalidAuditEventError, match="timestamp"):
        AuditEvent(
            event_id="e1",
            tenant_id="t1",
            actor="u1",
            event_type=AuditEventType.ACTION_REQUESTED,
            timestamp=datetime(2026, 1, 1, 12, 0, 0),  # noqa: DTZ001
            data={},
            previous_event_hash=GENESIS_HASH,
            event_hash="hash",
        )


def test_canonical_json_determinism() -> None:
    dict1: dict[str, Any] = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
    dict2: dict[str, Any] = {"a": 1, "c": {"y": 25, "z": 26}, "b": 2}

    json1 = AuditHasher.canonical_json(dict1)
    json2 = AuditHasher.canonical_json(dict2)
    assert json1 == json2
    assert json1 == '{"a":1,"b":2,"c":{"y":25,"z":26}}'


def test_sensitive_field_redactor() -> None:
    raw_data: dict[str, Any] = {
        "user": "alice",
        "card_number": "4111111111111111",
        "cvv": "123",
        "nested": {
            "api_key": "secret_abc",
            "safe_field": "public_data",
        },
    }
    redacted = SensitiveFieldRedactor.redact(raw_data)
    assert redacted["user"] == "alice"
    assert redacted["card_number"] == "[REDACTED]"
    assert redacted["cvv"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["safe_field"] == "public_data"
    # Ensure original was not mutated
    assert raw_data["card_number"] == "4111111111111111"


def test_in_memory_audit_ledger_chaining_and_verification() -> None:
    ledger = InMemoryAuditLedger()
    tenant = "tenant_test"

    e1 = ledger.record_event(
        event_type=AuditEventType.AGENT_AUTHENTICATED,
        action_id=None,
        tenant_id=tenant,
        actor="agent_01",
        data={"auth_method": "mtls"},
    )
    assert e1.previous_event_hash == GENESIS_HASH

    e2 = ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_01",
        tenant_id=tenant,
        actor="agent_01",
        data={"action": "purchase", "amount_minor": 5000},
    )
    assert e2.previous_event_hash == e1.event_hash

    e3 = ledger.record_event(
        event_type=AuditEventType.POLICY_EVALUATED,
        action_id="act_01",
        tenant_id=tenant,
        actor="policy_engine",
        data={"decision": "ALLOW"},
    )
    assert e3.previous_event_hash == e2.event_hash

    # Verify chain integrity
    assert ledger.verify_chain(tenant) is True

    # Check event retrieval
    all_events = ledger.get_events(tenant)
    assert len(all_events) == 3
    filtered = ledger.get_events(tenant, action_id="act_01")
    assert len(filtered) == 2


def test_tamper_detection_in_audit_chain() -> None:
    ledger = InMemoryAuditLedger()
    tenant = "tenant_secure"

    ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_01",
        tenant_id=tenant,
        actor="agent_01",
        data={"amount_minor": 1000},
    )
    ledger.record_event(
        event_type=AuditEventType.PAYMENT_CREATED,
        action_id="act_01",
        tenant_id=tenant,
        actor="system",
        data={"payment_id": "pay_01"},
    )

    assert ledger.verify_chain(tenant) is True

    # Tamper with internal event data
    internal_events = ledger._tenant_events[tenant]
    tampered_data = dict(internal_events[0].data)
    tampered_data["amount_minor"] = 9999999  # attacker modified amount
    object.__setattr__(internal_events[0], "data", tampered_data)

    # Chain verification must fail closed
    assert ledger.verify_chain(tenant) is False


def test_multi_tenant_audit_isolation() -> None:
    ledger = InMemoryAuditLedger()
    t1 = "tenant_1"
    t2 = "tenant_2"

    ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_t1",
        tenant_id=t1,
        actor="agent_t1",
        data={"context": "t1"},
    )
    ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_t2",
        tenant_id=t2,
        actor="agent_t2",
        data={"context": "t2"},
    )

    events_t1 = ledger.get_events(t1)
    events_t2 = ledger.get_events(t2)

    assert len(events_t1) == 1
    assert len(events_t2) == 1
    assert events_t1[0].tenant_id == t1
    assert events_t2[0].tenant_id == t2
    assert ledger.verify_chain(t1) is True
    assert ledger.verify_chain(t2) is True


@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=15))
def test_hypothesis_audit_chain_arbitrary_events(event_names: list[str]) -> None:
    ledger = InMemoryAuditLedger()
    tenant = "hypothesis_tenant"

    for idx, name in enumerate(event_names):
        ledger.record_event(
            event_type=AuditEventType.ACTION_REQUESTED,
            action_id=f"act_{idx}",
            tenant_id=tenant,
            actor="hypothesis_runner",
            data={"event_name": name, "seq": idx},
        )

    assert ledger.verify_chain(tenant) is True
    assert len(ledger.get_events(tenant)) == len(event_names)

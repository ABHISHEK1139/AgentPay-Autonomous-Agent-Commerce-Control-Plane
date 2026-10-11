"""Unit tests for distributed trace correlation and observability (§47).

Verifies:
- Propagation of caller-supplied X-Trace-ID to HTTP response headers and response body.
- Auto-generation of trc_* trace IDs when X-Trace-ID is omitted.
- End-to-end trace correlation in AuditEvent records across purchase flows and tool execution.
- Preservation of trace_id in standardized ApiErrorDetail error envelopes.
- Cryptographic hash-chain integrity binding of trace_id in AuditHasher and CLI verifier.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.audit import (
    GENESIS_HASH,
    AuditEventType,
    AuditHasher,
)


def _make_action_payload(action_id: str = "act_trace_01") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "domain": "commerce",
        "action": "purchase",
        "resource_type": "product",
        "resource_id": "prod_01",
        "amount_minor": 149900,
        "currency": "INR",
        "intent_purpose": "procurement",
        "variant_id": "var_01",
        "quantity": 1,
    }


def test_incoming_trace_id_propagated_to_headers_and_body() -> None:
    """X-Trace-ID header is preserved and returned in HTTP headers and response body."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    trace_id = "trc_custom_inbound_987654"
    headers = {
        "x-tenant-id": "tenant_01",
        "x-trace-id": trace_id,
    }

    resp = client.post("/api/v1/actions", json_body=_make_action_payload(), headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("x-trace-id") == trace_id
    assert resp.body.get("trace_id") == trace_id

    # Verify every emitted AuditEvent carries the exact trace_id
    events = orch._audit_ledger.get_events("tenant_01")  # type: ignore[union-attr]
    assert len(events) > 0
    for event in events:
        assert event.trace_id == trace_id


def test_omitted_trace_id_auto_generates_valid_trace_id() -> None:
    """When X-Trace-ID is omitted, app generates a trc_* ID returned in headers and body."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    headers = {"x-tenant-id": "tenant_01"}
    resp = client.post(
        "/api/v1/actions", json_body=_make_action_payload("act_auto_trace"), headers=headers
    )
    assert resp.status_code == 200

    generated_trace = resp.headers.get("x-trace-id")
    assert generated_trace is not None
    assert generated_trace.startswith("trace_")
    assert resp.body.get("trace_id") == generated_trace

    # Verify audit events recorded the generated trace ID
    events = orch._audit_ledger.get_events("tenant_01")  # type: ignore[union-attr]
    assert len(events) > 0
    for event in events:
        assert event.trace_id == generated_trace


def test_trace_id_in_error_responses() -> None:
    """Standardized error responses carry trace_id in headers and error envelope."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    custom_trace = "trc_err_boundary_test_42"
    headers = {
        "x-trace-id": custom_trace,
        # Omit x-tenant-id to trigger MISSING_TENANT_HEADER error
    }

    resp = client.get("/agent/catalog", headers=headers)
    assert resp.status_code == 400
    assert resp.headers.get("x-trace-id") == custom_trace
    assert "error" in resp.body
    assert resp.body["error"]["trace_id"] == custom_trace


def test_trace_id_in_tool_execution() -> None:
    """Tool invocation propagates trace_id to response body, headers, and audit ledger."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    custom_trace = "trc_tool_search_11"
    headers = {
        "x-tenant-id": "tenant_01",
        "x-trace-id": custom_trace,
    }
    tool_body = {
        "agent_id": "agent_01",
        "parameters": {"query": "laptop"},
    }

    resp = client.post("/api/v1/tools/search_products/invoke", json_body=tool_body, headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("x-trace-id") == custom_trace
    assert resp.body.get("trace_id") == custom_trace

    events = orch._audit_ledger.get_events("tenant_01")  # type: ignore[union-attr]
    assert len(events) >= 1
    assert any(
        e.event_type == AuditEventType.ACTION_REQUESTED and e.trace_id == custom_trace
        for e in events
    )


def test_trace_id_in_protocol_adapters() -> None:
    """Protocol adapter endpoints propagate trace_id to response headers and body."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    custom_trace = "trc_proto_acp_77"
    headers = {
        "x-tenant-id": "tenant_01",
        "x-trace-id": custom_trace,
    }

    acp_payload = {
        "acp_version": "1.0",
        "request_id": "acp_req_trace_01",
        "timestamp": "2026-10-06T18:30:00Z",
        "buyer": {
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
        },
        "cart": {
            "items": [
                {
                    "product_id": "prod_01",
                    "variant_id": "var_01",
                    "sku": "SKU-PROD-01",
                    "name": "Pro Workstation",
                    "quantity": 1,
                    "unit_price_minor": 149900,
                    "currency": "INR",
                }
            ]
        },
        "intent": {
            "purpose": "developer workstation accessories",
            "budget_max_minor": 150000,
        },
        "execution": {
            "mode": "autonomous",
            "idempotency_key": "acp_idem_trace_01",
        },
    }

    resp = client.post("/api/v1/protocols/acp/checkout", json_body=acp_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("x-trace-id") == custom_trace
    assert resp.body.get("trace_id") == custom_trace


def test_audit_chain_verification_with_trace_id() -> None:
    """Audit ledger cryptographic chain verification passes with trace_id in preimage."""
    ledger = InMemoryAuditLedger()
    tenant = "tenant_trace_verify"

    ev1 = ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_01",
        tenant_id=tenant,
        actor="agent_01",
        data={"action": "buy"},
        trace_id="trc_chain_step_1",
    )
    ev2 = ledger.record_event(
        event_type=AuditEventType.PAYMENT_CREATED,
        action_id="act_01",
        tenant_id=tenant,
        actor="payment_service",
        data={"status": "SUCCESS"},
        trace_id="trc_chain_step_1",
    )

    assert ev1.trace_id == "trc_chain_step_1"
    assert ev2.trace_id == "trc_chain_step_1"
    assert ledger.verify_chain(tenant) is True

    # Tampering with trace_id invalidates cryptographic hash chain
    object.__setattr__(ev1, "trace_id", "forged_trace_id")
    assert ledger.verify_chain(tenant) is False

    # Restoring original trace_id restores chain validity
    object.__setattr__(ev1, "trace_id", "trc_chain_step_1")
    assert ledger.verify_chain(tenant) is True


def test_cli_verifier_verifies_and_detects_trace_id_tamper(tmp_path: Any) -> None:
    """Independent CLI verification script verifies trace_id and detects tampering."""
    tenant = "tenant_cli_trace"
    now = datetime.now(UTC).isoformat()
    trace_id = "trc_cli_audit_record"

    hash_0 = AuditHasher.compute_event_hash(
        event_data={"item": "widget"},
        previous_hash=GENESIS_HASH,
        actor="agent_01",
        event_type="ACTION_REQUESTED",
        timestamp=now,
        tenant_id=tenant,
        trace_id=trace_id,
    )

    valid_events = [
        {
            "event_id": "evt_001",
            "tenant_id": tenant,
            "actor": "agent_01",
            "event_type": "ACTION_REQUESTED",
            "timestamp": now,
            "data": {"item": "widget"},
            "previous_event_hash": GENESIS_HASH,
            "event_hash": hash_0,
            "trace_id": trace_id,
        }
    ]

    valid_file = tmp_path / "valid_audit.jsonl"
    valid_file.write_text(
        "\n".join(json.dumps(ev) for ev in valid_events) + "\n",
        encoding="utf-8",
    )

    res_valid = subprocess.run(
        ["uv", "run", "python", "scripts/verify_audit_chain.py", str(valid_file)],
        capture_output=True,
        text=True,
    )
    assert res_valid.returncode == 0
    assert "SUCCESS" in res_valid.stdout or "verified" in res_valid.stdout

    # Now tamper trace_id in the JSONL file
    tampered_events = [
        {
            **valid_events[0],
            "trace_id": "forged_trace_99",
        }
    ]
    tampered_file = tmp_path / "tampered_audit.jsonl"
    tampered_file.write_text(
        "\n".join(json.dumps(ev) for ev in tampered_events) + "\n",
        encoding="utf-8",
    )

    res_tampered = subprocess.run(
        ["uv", "run", "python", "scripts/verify_audit_chain.py", str(tampered_file)],
        capture_output=True,
        text=True,
    )
    assert res_tampered.returncode == 1
    assert "tamper detected" in (res_tampered.stdout + res_tampered.stderr).lower()

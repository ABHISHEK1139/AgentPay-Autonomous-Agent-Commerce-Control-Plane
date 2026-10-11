"""Comprehensive test suite for Sprint 11 Agent Protocol Adapters (§107, §108).

Validates:
- Inbound translation: ACP, AP2, UAP, x402 mapping to canonical ActionRequest.
- Outbound translation: Execution result mapping to protocol-compliant responses.
- Error handling: malformed payloads, invalid currency, negative amounts, unsupported versions, unsupported actions.
- End-to-end integration through PurchaseOrchestrator and HTTP API layer.
- Multi-tenancy enforcement and emergency kill switch protection.
- Architecture boundary enforcement (§10).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.protocols import (
    ACPAdapter,
    AP2Adapter,
    MalformedPayloadError,
    UAPAdapter,
    UnsupportedActionError,
    UnsupportedProtocolVersionError,
    X402Adapter,
    X402Challenge,
    format_error_envelope,
    parse_iso_utc,
)
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.application.use_cases.purchase_flow import PurchaseExecutionResult
from agentpay.domain.action_request import ActionStatus, AuthorizationMode
from agentpay.domain.errors import InvalidActionRequestError
from agentpay.domain.policy import PolicyDecision

# ---------------------------------------------------------------------------
# Inbound & Outbound Unit Tests: ACP
# ---------------------------------------------------------------------------


def test_acp_adapter_inbound_and_outbound_happy_path() -> None:
    adapter = ACPAdapter()
    raw_payload: dict[str, Any] = {
        "acp_version": "1.0",
        "request_id": "acp_req_001",
        "timestamp": "2026-10-06T18:30:00Z",
        "buyer": {
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
            "delegation_token": "del_tok_123",
        },
        "cart": {
            "items": [
                {
                    "product_id": "prod_01",
                    "variant_id": "var_01",
                    "sku": "SKU-PROD-01",
                    "name": "Pro Workstation",
                    "quantity": 1,
                    "unit_price_minor": 129900,
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
            "idempotency_key": "acp_idem_001",
        },
    }

    req = adapter.parse_payload(raw_payload)
    assert req.acp_version == "1.0"
    assert req.request_id == "acp_req_001"
    assert req.buyer.agent_id == "agent_01"
    assert len(req.items) == 1
    assert req.items[0].unit_price_minor == 129900

    action, metadata = adapter.to_action_request(req, tenant_id="tenant_01")
    assert action.action_id == "acp_req_001"
    assert action.tenant_id == "tenant_01"
    assert action.principal_id == "user_01"
    assert action.agent_id == "agent_01"
    assert action.domain == "commerce"
    assert action.action == "purchase"
    assert action.resource.type == "product"
    assert action.resource.id == "prod_01"
    assert action.amount is not None
    assert action.amount.minor_units == 129900
    assert action.amount.currency == "INR"
    assert action.intent.budget_max is not None
    assert action.intent.budget_max.minor_units == 150000
    assert action.authorization_mode == AuthorizationMode.AUTONOMOUS
    assert metadata["variant_id"] == "var_01"
    assert metadata["quantity"] == 1
    assert metadata["idempotency_key"] == "acp_idem_001"

    # Outbound result mapping
    mock_result = PurchaseExecutionResult(
        action_id=action.action_id,
        tenant_id=action.tenant_id,
        status=ActionStatus.COMPLETED,
        decision=PolicyDecision.ALLOW,
    )
    resp = adapter.from_execution_result(req, mock_result)
    assert resp.status == "COMPLETED"
    assert resp.decision == "ALLOW"
    assert resp.request_id == "acp_req_001"
    assert resp.to_dict()["status"] == "COMPLETED"


def test_acp_adapter_multi_item_and_currency_validation() -> None:
    adapter = ACPAdapter()
    raw_payload: dict[str, Any] = {
        "acp_version": "1.0",
        "request_id": "acp_req_multi",
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
                    "unit_price_minor": 1000,
                    "quantity": 2,
                    "currency": "INR",
                },
                {
                    "product_id": "prod_02",
                    "variant_id": "var_02",
                    "unit_price_minor": 2000,
                    "quantity": 3,
                    "currency": "INR",
                },
            ]
        },
    }
    req = adapter.parse_payload(raw_payload)
    action, _meta = adapter.to_action_request(req)
    # Total = 1000*2 + 2000*3 = 8000
    assert action.amount is not None
    assert action.amount.minor_units == 8000

    # Currency mismatch test
    raw_mixed_currency = {
        "acp_version": "1.0",
        "request_id": "acp_req_err",
        "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "t"},
        "cart": {
            "items": [
                {"product_id": "p1", "unit_price_minor": 100, "quantity": 1, "currency": "INR"},
                {"product_id": "p2", "unit_price_minor": 100, "quantity": 1, "currency": "USD"},
            ]
        },
    }
    with pytest.raises(MalformedPayloadError, match="multiple currencies"):
        adapter.parse_payload(raw_mixed_currency)


def test_acp_adapter_validation_errors() -> None:
    adapter = ACPAdapter()

    # Unsupported version
    with pytest.raises(UnsupportedProtocolVersionError):
        adapter.parse_payload({"acp_version": "9.9", "request_id": "r1"})

    # Missing buyer
    with pytest.raises(MalformedPayloadError, match="buyer"):
        adapter.parse_payload({"acp_version": "1.0", "request_id": "r1"})

    # Negative price
    with pytest.raises(MalformedPayloadError, match="unit_price_minor"):
        adapter.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "r1",
                "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": -100, "currency": "INR"}]
                },
            }
        )

    # Budget cap exceeded in domain model constructor
    req_over_budget = adapter.parse_payload(
        {
            "acp_version": "1.0",
            "request_id": "r_budget_err",
            "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "t"},
            "intent": {"budget_max_minor": 500},
            "cart": {"items": [{"product_id": "p1", "unit_price_minor": 1000, "currency": "INR"}]},
        }
    )
    with pytest.raises(InvalidActionRequestError, match="exceeds stated intent budget"):
        adapter.to_action_request(req_over_budget)


# ---------------------------------------------------------------------------
# Inbound & Outbound Unit Tests: AP2
# ---------------------------------------------------------------------------


def test_ap2_adapter_inbound_and_outbound_happy_path() -> None:
    adapter = AP2Adapter()
    raw_payload: dict[str, Any] = {
        "ap2_version": "1.0",
        "payment_request_id": "pay_mandate_01",
        "mandate_id": "man_7721",
        "payer": {
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
        },
        "payee": {
            "merchant_id": "merch_acme",
            "order_ref": "ord_cloud_99",
            "resource_type": "invoice",
        },
        "amount": {
            "minor_units": 45000,
            "currency": "INR",
        },
        "purpose": "Server hosting settlement",
        "constraints": {
            "max_authorized_minor": 50000,
            "authorization_mode": "AUTONOMOUS",
        },
        "idempotency_key": "ap2_idem_99",
    }

    req = adapter.parse_payload(raw_payload)
    assert req.ap2_version == "1.0"
    assert req.payment_request_id == "pay_mandate_01"
    assert req.mandate_id == "man_7721"
    assert req.amount_minor == 45000

    action, metadata = adapter.to_action_request(req, tenant_id="tenant_01")
    assert action.action_id == "ap2_pay_mandate_01"
    assert action.domain == "payments"
    assert action.action == "purchase"
    assert action.resource.type == "invoice"
    assert action.resource.id == "ord_cloud_99"
    assert action.amount is not None
    assert action.amount.minor_units == 45000
    assert metadata["idempotency_key"] == "ap2_idem_99"

    mock_result = PurchaseExecutionResult(
        action_id=action.action_id,
        tenant_id=action.tenant_id,
        status=ActionStatus.COMPLETED,
        decision=PolicyDecision.ALLOW,
    )
    resp = adapter.from_execution_result(req, mock_result)
    assert resp.status == "COMPLETED"
    assert resp.decision == "ALLOW"
    assert resp.to_dict()["payment_request_id"] == "pay_mandate_01"


def test_ap2_adapter_validation_errors() -> None:
    adapter = AP2Adapter()

    # Unsupported version
    with pytest.raises(UnsupportedProtocolVersionError):
        adapter.parse_payload({"ap2_version": "0.1", "payment_request_id": "p1"})

    # Missing payer
    with pytest.raises(MalformedPayloadError, match="payer"):
        adapter.parse_payload({"ap2_version": "1.0", "payment_request_id": "p1"})

    # Negative amount
    with pytest.raises(MalformedPayloadError, match="negative"):
        adapter.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "p1",
                "payer": {"principal_id": "u", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount": {"minor_units": -500, "currency": "INR"},
            }
        )


# ---------------------------------------------------------------------------
# Inbound & Outbound Unit Tests: UAP
# ---------------------------------------------------------------------------


def test_uap_adapter_inbound_and_outbound_happy_path() -> None:
    adapter = UAPAdapter()
    raw_payload: dict[str, Any] = {
        "uap_version": "1.0",
        "message_id": "msg_rpc_441",
        "header": {
            "sender": "urn:agentpay:agent:agent_01",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {
                "product_id": "prod_01",
                "variant_id": "var_01",
                "quantity": 1,
                "price_minor": 129900,
                "currency": "INR",
                "purpose": "Mechanical keyboard",
                "budget_cap_minor": 150000,
            },
            "constraints": {
                "execution_mode": "AUTONOMOUS",
                "idempotency_key": "uap_idem_01",
            },
        },
    }

    req = adapter.parse_payload(raw_payload)
    assert req.uap_version == "1.0"
    assert req.capability == "commerce.purchase"

    action, metadata = adapter.to_action_request(req, tenant_id="tenant_01")
    assert action.action_id == "uap_msg_rpc_441"
    assert action.agent_id == "agent_01"  # Stripped from URN
    assert action.domain == "commerce"
    assert action.action == "purchase"
    assert action.resource.id == "prod_01"
    assert action.amount is not None
    assert action.amount.minor_units == 129900
    assert metadata["variant_id"] == "var_01"

    mock_result = PurchaseExecutionResult(
        action_id=action.action_id,
        tenant_id=action.tenant_id,
        status=ActionStatus.COMPLETED,
        decision=PolicyDecision.ALLOW,
    )
    resp = adapter.from_execution_result(req, mock_result)
    assert resp.status == "SUCCESS"
    assert resp.error is None
    assert resp.result is not None
    assert resp.result["decision"] == "ALLOW"
    assert resp.to_dict()["result"]["status"] == "COMPLETED"


def test_uap_adapter_validation_and_unsupported_action() -> None:
    adapter = UAPAdapter()

    # Unsupported action / capability format
    raw_invalid_cap = {
        "uap_version": "1.0",
        "message_id": "m1",
        "header": {"sender": "s", "tenant": "t", "principal": "p"},
        "payload": {"capability": "singleword", "parameters": {"product_id": "p1"}},
    }
    req = adapter.parse_payload(raw_invalid_cap)
    with pytest.raises(UnsupportedActionError, match=r"format 'domain\.action'"):
        adapter.to_action_request(req)

    # Missing product_id
    raw_missing_prod = {
        "uap_version": "1.0",
        "message_id": "m1",
        "header": {"sender": "s", "tenant": "t", "principal": "p"},
        "payload": {"capability": "commerce.purchase", "parameters": {}},
    }
    req_no_prod = adapter.parse_payload(raw_missing_prod)
    with pytest.raises(MalformedPayloadError, match="product_id"):
        adapter.to_action_request(req_no_prod)


# ---------------------------------------------------------------------------
# Inbound & Outbound Unit Tests: x402
# ---------------------------------------------------------------------------


def test_x402_adapter_inbound_and_outbound_happy_path() -> None:
    adapter = X402Adapter()

    # Challenge header generation test
    chal = X402Challenge(
        challenge_id="chal_99",
        resource_uri="https://api.example.com/data",
        amount_minor=2500,
        currency="INR",
        payee_id="merchant_01",
    )
    hdr_str = chal.to_header()
    assert 'challenge_id="chal_99"' in hdr_str
    assert "amount=2500" in hdr_str

    raw_req: dict[str, Any] = {
        "x402_version": "1.0",
        "challenge_id": "chal_99",
        "resource_uri": "https://api.example.com/data",
        "payee_id": "merchant_01",
        "amount_minor": 2500,
        "currency": "INR",
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
        "purpose": "Streaming API access",
    }

    req = adapter.parse_payload(raw_req)
    assert req.x402_version == "1.0"
    assert req.challenge_id == "chal_99"

    action, metadata = adapter.to_action_request(req, tenant_id="tenant_01")
    assert action.action_id == "x402_chal_99"
    assert action.domain == "web"
    assert action.action == "purchase"
    assert action.resource.type == "uri"
    assert action.resource.id == "https://api.example.com/data"
    assert action.amount is not None
    assert action.amount.minor_units == 2500
    assert metadata["variant_id"] is not None

    from agentpay.domain.payments import PaymentAttempt, PaymentStatus

    mock_payment = PaymentAttempt(
        attempt_id="pay_att_x402_1",
        intent_id="intent_01",
        tenant_id="tenant_01",
        amount=action.amount,
        status=PaymentStatus.CAPTURED,
        idempotency_key="idem_x402_1",
        provider="mock",
        created_at=datetime.now(UTC),
    )
    mock_result = PurchaseExecutionResult(
        action_id=action.action_id,
        tenant_id=action.tenant_id,
        status=ActionStatus.COMPLETED,
        decision=PolicyDecision.ALLOW,
        payment=mock_payment,
    )
    resp = adapter.from_execution_result(req, mock_result)
    assert resp.status == "PAID"
    assert resp.authorization_token is not None
    assert resp.authorization_token.startswith("x402_")
    assert resp.settlement_ref == "pay_att_x402_1"
    assert resp.expires_at is not None
    assert resp.to_dict()["status"] == "PAID"


def test_x402_adapter_validation_errors() -> None:
    adapter = X402Adapter()

    # Unsupported version
    with pytest.raises(UnsupportedProtocolVersionError):
        adapter.parse_payload({"x402_version": "0.5", "challenge_id": "c1"})

    # Negative amount
    with pytest.raises(MalformedPayloadError, match="negative"):
        adapter.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "c1",
                "resource_uri": "https://foo.com",
                "amount_minor": -10,
                "agent_id": "a",
                "principal_id": "p",
                "tenant_id": "t",
            }
        )


# ---------------------------------------------------------------------------
# HTTP API Integration Tests: ACP Route
# ---------------------------------------------------------------------------


def test_api_acp_checkout_endpoint_success_and_errors() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # 1. Successful under-limit checkout
    payload = {
        "acp_version": "1.0",
        "request_id": "api_acp_01",
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
                    "unit_price_minor": 129900,
                    "quantity": 1,
                    "currency": "INR",
                }
            ]
        },
    }
    status_code, body = client.post(
        "/api/v1/protocols/acp/checkout", json_body=payload, headers=headers
    )
    assert status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["order_id"] is not None
    assert body["payment_id"] is not None

    # 2. Malformed payload error
    malformed_payload = {"acp_version": "1.0", "request_id": "api_acp_err"}
    status_code, err_body = client.post(
        "/api/v1/protocols/acp/checkout", json_body=malformed_payload, headers=headers
    )
    assert status_code == 422
    assert err_body["error"]["code"] == "MALFORMED_PAYLOAD"

    # 3. Unsupported version error
    unsupported_payload = {
        "acp_version": "9.0",
        "request_id": "api_acp_err",
        "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "tenant_01"},
        "cart": {"items": [{"product_id": "prod_01", "unit_price_minor": 100, "currency": "INR"}]},
    }
    status_code, err_body = client.post(
        "/api/v1/protocols/acp/checkout", json_body=unsupported_payload, headers=headers
    )
    assert status_code == 400
    assert err_body["error"]["code"] == "UNSUPPORTED_PROTOCOL_VERSION"

    # 4. Cross-tenant access rejection
    status_code, err_body = client.post(
        "/api/v1/protocols/acp/checkout",
        json_body=payload,
        headers={"x-tenant-id": "tenant_other"},
    )
    assert status_code == 403
    assert err_body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"

    # 5. Tenant resolution from payload when header is omitted
    status_code, body = client.post(
        "/api/v1/protocols/acp/checkout",
        json_body={
            **payload,
            "request_id": "api_acp_no_hdr",
        },
    )
    assert status_code == 200
    assert body["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# HTTP API Integration Tests: AP2 Route
# ---------------------------------------------------------------------------


def test_api_ap2_mandates_endpoint_under_and_over_limit() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # 1. Under-limit mandate (autonomous execution)
    payload_under = {
        "ap2_version": "1.0",
        "payment_request_id": "api_ap2_under",
        "mandate_id": "mandate_100",
        "payer": {
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
        },
        "payee": {"merchant_id": "merch_acme", "order_ref": "var_01"},
        "amount": {"minor_units": 129900, "currency": "INR"},
    }
    status_code, body = client.post(
        "/api/v1/protocols/ap2/mandates", json_body=payload_under, headers=headers
    )
    assert status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["transaction_id"] is not None

    # 2. Over-autonomous-limit mandate (₹2,500 > ₹2,000 threshold -> triggers REVIEW)
    payload_over = {
        "ap2_version": "1.0",
        "payment_request_id": "api_ap2_over",
        "mandate_id": "mandate_200",
        "payer": {
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
        },
        "payee": {"merchant_id": "merch_acme", "order_ref": "inv_mandate_over"},
        "amount": {"minor_units": 250000, "currency": "INR"},
    }
    status_code, body = client.post(
        "/api/v1/protocols/ap2/mandates", json_body=payload_over, headers=headers
    )
    assert status_code == 200
    assert body["status"] == "AWAITING_APPROVAL"
    assert body["decision"] == "REVIEW"
    assert body["approval_id"] is not None


# ---------------------------------------------------------------------------
# HTTP API Integration Tests: UAP Route
# ---------------------------------------------------------------------------


def test_api_uap_rpc_endpoint() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "uap_version": "1.0",
        "message_id": "uap_msg_api_01",
        "header": {
            "sender": "urn:agentpay:agent:agent_01",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {
                "product_id": "prod_01",
                "variant_id": "var_01",
                "price_minor": 129900,
                "currency": "INR",
            },
        },
    }
    status_code, body = client.post("/api/v1/protocols/uap/rpc", json_body=payload, headers=headers)
    assert status_code == 200
    assert body["status"] == "SUCCESS"
    assert body["error"] is None
    assert body["result"]["decision"] == "ALLOW"

    # Unsupported action
    bad_cap_payload = {
        **payload,
        "payload": {
            "capability": "unsupported",
            "parameters": {"product_id": "prod_01"},
        },
    }
    status_code, err_body = client.post(
        "/api/v1/protocols/uap/rpc", json_body=bad_cap_payload, headers=headers
    )
    assert status_code == 400
    assert err_body["error"]["code"] == "UNSUPPORTED_ACTION"


# ---------------------------------------------------------------------------
# HTTP API Integration Tests: x402 Route
# ---------------------------------------------------------------------------


def test_api_x402_pay_endpoint() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "x402_version": "1.0",
        "challenge_id": "chal_402_api",
        "resource_uri": "https://api.marketdata.com/stream",
        "payee_id": "merch_data",
        "amount_minor": 1000,
        "currency": "INR",
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
        "purpose": "Market data stream",
    }
    status_code, body = client.post(
        "/api/v1/protocols/x402/pay", json_body=payload, headers=headers
    )
    assert status_code == 200
    assert body["status"] == "PAID"
    assert body["authorization_token"] is not None
    assert body["settlement_ref"] is not None
    assert body["expires_at"] is not None


# ---------------------------------------------------------------------------
# Emergency Kill Switch Integration with Protocol Endpoints (§21, §67)
# ---------------------------------------------------------------------------


def test_emergency_kill_switch_blocks_all_protocol_endpoints() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # Activate emergency kill switch (§21, §67)
    app.pause_emergency(operator_id="sec_admin", reason="Security threat containment")

    endpoints: list[tuple[str, dict[str, Any]]] = [
        (
            "/api/v1/protocols/acp/checkout",
            {
                "acp_version": "1.0",
                "request_id": "r1",
                "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "tenant_01"},
                "cart": {
                    "items": [{"product_id": "p", "unit_price_minor": 100, "currency": "INR"}]
                },
            },
        ),
        (
            "/api/v1/protocols/ap2/mandates",
            {
                "ap2_version": "1.0",
                "payment_request_id": "r2",
                "payer": {"principal_id": "u", "agent_id": "a", "tenant_id": "tenant_01"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount": {"minor_units": 100, "currency": "INR"},
            },
        ),
        (
            "/api/v1/protocols/uap/rpc",
            {
                "uap_version": "1.0",
                "message_id": "r3",
                "header": {"sender": "s", "tenant": "tenant_01", "principal": "p"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "p", "price_minor": 100},
                },
            },
        ),
        (
            "/api/v1/protocols/x402/pay",
            {
                "x402_version": "1.0",
                "challenge_id": "r4",
                "resource_uri": "https://data.com",
                "amount_minor": 100,
                "agent_id": "a",
                "principal_id": "p",
                "tenant_id": "tenant_01",
            },
        ),
    ]

    for path, payload in endpoints:
        status, body = client.post(path, json_body=payload, headers=headers)
        assert status == 503
        assert body["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"

    # Resume emergency operations
    app.resume_emergency(operator_id="sec_admin")
    status, body = client.post(endpoints[0][0], json_body=endpoints[0][1], headers=headers)
    assert status == 200


# ---------------------------------------------------------------------------
# Error Envelope & Parser Utility Tests
# ---------------------------------------------------------------------------


def test_format_error_envelope_and_timestamp_parser() -> None:
    env = format_error_envelope(
        code="TEST_ERROR", message="Something failed", request_id="req_999", details={"key": "val"}
    )
    assert env["error"]["code"] == "TEST_ERROR"
    assert env["error"]["message"] == "Something failed"
    assert env["error"]["request_id"] == "req_999"
    assert env["error"]["details"] == {"key": "val"}

    # Timestamp parser tests
    parsed_utc = parse_iso_utc("2026-10-06T18:30:00Z")
    assert parsed_utc.tzinfo == UTC
    assert parsed_utc.year == 2026

    # Current time default
    now_utc = parse_iso_utc(None)
    assert now_utc.tzinfo == UTC

    # Malformed timestamp
    with pytest.raises(MalformedPayloadError, match="Invalid timestamp"):
        parse_iso_utc("invalid-timestamp-string")


# ---------------------------------------------------------------------------
# Domain Boundary Purity Verification (§10)
# ---------------------------------------------------------------------------


def test_domain_layer_never_imports_protocol_adapters() -> None:
    domain_dir = Path(__file__).resolve().parents[2] / "src" / "agentpay" / "domain"
    for py_file in domain_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "protocols" not in alias.name
                    assert "adapters" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "protocols" not in node.module
                assert "adapters" not in node.module

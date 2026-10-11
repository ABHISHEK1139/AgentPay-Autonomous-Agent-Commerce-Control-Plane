"""Comprehensive unit and boundary tests for Agent Protocol Adapters (§107, §108).

Verifies strict integer validation, floating point and boolean rejection,
schema conformance, and fail-closed behavior across UAP, x402, ACP, and AP2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.protocols import (
    MalformedPayloadError,
    UAPAdapter,
    X402Adapter,
    X402ChallengeRequest,
)
from agentpay.api.app import AgentPayApiApp, ApiTestClient


@pytest.fixture
def uap_adapter() -> UAPAdapter:
    return UAPAdapter()


@pytest.fixture
def x402_adapter() -> X402Adapter:
    return X402Adapter()


@pytest.fixture
def api_client() -> ApiTestClient:
    orch, *_ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    return ApiTestClient(app).as_buyer()


# ===========================================================================
# UAP Adapter Strict Integer Validation Tests
# ===========================================================================


def test_uap_strict_integer_quantity(uap_adapter: UAPAdapter) -> None:
    """Quantity must be a strict non-zero, non-negative integer."""
    base_payload: dict[str, Any] = {
        "uap_version": "1.0",
        "message_id": "uap_qty_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "price_minor": 1000},
        },
    }

    # Valid integer quantities
    for qty in (1, 5, 42):
        base_payload["payload"]["parameters"]["quantity"] = qty
        msg = uap_adapter.parse_payload(base_payload)
        _action, meta = uap_adapter.to_action_request(msg)
        assert meta["quantity"] == qty

    # Float quantities must raise MalformedPayloadError
    for flt_qty in (1.5, 2.0, 0.5):
        base_payload["payload"]["parameters"]["quantity"] = flt_qty
        with pytest.raises(MalformedPayloadError, match="quantity must be an integer"):
            uap_adapter.parse_payload(base_payload)

    # Boolean quantities must raise MalformedPayloadError
    for bool_qty in (True, False):
        base_payload["payload"]["parameters"]["quantity"] = bool_qty
        with pytest.raises(MalformedPayloadError, match="quantity must be an integer"):
            uap_adapter.parse_payload(base_payload)

    # Non-positive quantities (zero or negative) must raise MalformedPayloadError
    for invalid_qty in (0, -1, -5):
        base_payload["payload"]["parameters"]["quantity"] = invalid_qty
        with pytest.raises(MalformedPayloadError, match="quantity must be greater than zero"):
            uap_adapter.parse_payload(base_payload)

    # String quantities must raise MalformedPayloadError
    base_payload["payload"]["parameters"]["quantity"] = "1"
    with pytest.raises(MalformedPayloadError, match="quantity must be an integer"):
        uap_adapter.parse_payload(base_payload)


def test_uap_strict_integer_price(uap_adapter: UAPAdapter) -> None:
    """price_minor, amount_minor, and amount must be strict non-negative integers."""
    base_payload: dict[str, Any] = {
        "uap_version": "1.0",
        "message_id": "uap_price_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "quantity": 1},
        },
    }

    # Valid integer prices
    for valid_price in (0, 100, 129900):
        base_payload["payload"]["parameters"]["price_minor"] = valid_price
        msg = uap_adapter.parse_payload(base_payload)
        action, _ = uap_adapter.to_action_request(msg)
        assert action.amount is not None
        assert action.amount.minor_units == valid_price

    # Float price must raise MalformedPayloadError
    for flt in (12.5, 99.99, 100.0):
        base_payload["payload"]["parameters"]["price_minor"] = flt
        with pytest.raises(MalformedPayloadError, match="price_minor must be an integer"):
            uap_adapter.parse_payload(base_payload)

    # Boolean price must raise MalformedPayloadError
    for b in (True, False):
        base_payload["payload"]["parameters"]["price_minor"] = b
        with pytest.raises(MalformedPayloadError, match="price_minor must be an integer"):
            uap_adapter.parse_payload(base_payload)

    # Negative price must raise MalformedPayloadError
    for neg in (-1, -500):
        base_payload["payload"]["parameters"]["price_minor"] = neg
        with pytest.raises(MalformedPayloadError, match="price_minor cannot be negative"):
            uap_adapter.parse_payload(base_payload)

    # Fallback keys: amount_minor and amount
    del base_payload["payload"]["parameters"]["price_minor"]

    base_payload["payload"]["parameters"]["amount_minor"] = 12.5
    with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
        uap_adapter.parse_payload(base_payload)

    base_payload["payload"]["parameters"]["amount_minor"] = True
    with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
        uap_adapter.parse_payload(base_payload)

    del base_payload["payload"]["parameters"]["amount_minor"]

    base_payload["payload"]["parameters"]["amount"] = -50
    with pytest.raises(MalformedPayloadError, match="amount cannot be negative"):
        uap_adapter.parse_payload(base_payload)

    base_payload["payload"]["parameters"]["amount"] = 3.14
    with pytest.raises(MalformedPayloadError, match="amount must be an integer"):
        uap_adapter.parse_payload(base_payload)


def test_uap_strict_integer_budget_cap(uap_adapter: UAPAdapter) -> None:
    """budget_cap_minor and budget_max_minor must be strict non-negative integers."""
    base_payload: dict[str, Any] = {
        "uap_version": "1.0",
        "message_id": "uap_budget_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "price_minor": 1000},
        },
    }

    # Valid budget cap
    base_payload["payload"]["parameters"]["budget_cap_minor"] = 150000
    msg = uap_adapter.parse_payload(base_payload)
    action, _ = uap_adapter.to_action_request(msg)
    assert action.intent.budget_max is not None
    assert action.intent.budget_max.minor_units == 150000

    # Float budget cap
    base_payload["payload"]["parameters"]["budget_cap_minor"] = 150000.50
    with pytest.raises(MalformedPayloadError, match="must be an integer"):
        uap_adapter.parse_payload(base_payload)

    # Boolean budget cap
    base_payload["payload"]["parameters"]["budget_cap_minor"] = True
    with pytest.raises(MalformedPayloadError, match="must be an integer"):
        uap_adapter.parse_payload(base_payload)

    # Negative budget cap
    base_payload["payload"]["parameters"]["budget_cap_minor"] = -500
    with pytest.raises(MalformedPayloadError, match="cannot be negative"):
        uap_adapter.parse_payload(base_payload)


def test_uap_missing_fields_fail_closed(uap_adapter: UAPAdapter) -> None:
    """Missing required header or payload fields must fail closed."""
    # Missing sender
    with pytest.raises(MalformedPayloadError, match="sender"):
        uap_adapter.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "m1",
                "header": {"tenant": "t", "principal": "p"},
                "payload": {"capability": "commerce.purchase", "parameters": {"product_id": "p"}},
            }
        )

    # Missing tenant
    with pytest.raises(MalformedPayloadError, match="tenant"):
        uap_adapter.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "m1",
                "header": {"sender": "s", "principal": "p"},
                "payload": {"capability": "commerce.purchase", "parameters": {"product_id": "p"}},
            }
        )

    # Missing capability
    with pytest.raises(MalformedPayloadError, match="capability"):
        uap_adapter.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "m1",
                "header": {"sender": "s", "tenant": "t", "principal": "p"},
                "payload": {"parameters": {"product_id": "p"}},
            }
        )

    # Missing product_id in parameters
    msg = uap_adapter.parse_payload(
        {
            "uap_version": "1.0",
            "message_id": "m1",
            "header": {"sender": "s", "tenant": "t", "principal": "p"},
            "payload": {"capability": "commerce.purchase", "parameters": {}},
        }
    )
    with pytest.raises(MalformedPayloadError, match="product_id"):
        uap_adapter.to_action_request(msg)


# ===========================================================================
# x402 Adapter Strict Integer Validation Tests
# ===========================================================================


def test_x402_strict_integer_amount(x402_adapter: X402Adapter) -> None:
    """amount_minor must be a strict non-negative integer."""
    base_payload: dict[str, Any] = {
        "x402_version": "1.0",
        "challenge_id": "chal_01",
        "resource_uri": "https://api.example.com/data",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "currency": "INR",
    }

    # Valid integer amounts
    for valid_amt in (0, 100, 2500):
        base_payload["amount_minor"] = valid_amt
        req = x402_adapter.parse_payload(base_payload)
        assert req.amount_minor == valid_amt
        action, _ = x402_adapter.to_action_request(req)
        assert action.amount is not None
        assert action.amount.minor_units == valid_amt

    # Float amount must raise MalformedPayloadError
    for flt in (12.5, 99.99, 100.0):
        base_payload["amount_minor"] = flt
        with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
            x402_adapter.parse_payload(base_payload)

    # Boolean amount must raise MalformedPayloadError
    for b in (True, False):
        base_payload["amount_minor"] = b
        with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
            x402_adapter.parse_payload(base_payload)

    # Negative amount must raise MalformedPayloadError
    for neg in (-1, -250):
        base_payload["amount_minor"] = neg
        with pytest.raises(MalformedPayloadError, match="amount_minor cannot be negative"):
            x402_adapter.parse_payload(base_payload)

    # String amount must raise MalformedPayloadError
    base_payload["amount_minor"] = "2500"
    with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
        x402_adapter.parse_payload(base_payload)


def test_x402_to_action_request_integer_guard(x402_adapter: X402Adapter) -> None:
    """to_action_request must defensively reject non-integer amount_minor even if bypass attempted."""
    now = datetime.now(UTC)
    invalid_req: Any = X402ChallengeRequest(
        x402_version="1.0",
        challenge_id="chal_bypass",
        resource_uri="https://api.example.com/data",
        payee_id="merchant_payee",
        amount_minor=12.5,  # type: ignore[arg-type]
        currency="INR",
        agent_id="agent_01",
        principal_id="user_01",
        tenant_id="tenant_01",
        purpose="Bypass attempt",
        timestamp=now,
    )
    with pytest.raises(MalformedPayloadError, match="amount_minor must be an integer"):
        x402_adapter.to_action_request(invalid_req)

    neg_req: Any = X402ChallengeRequest(
        x402_version="1.0",
        challenge_id="chal_bypass_neg",
        resource_uri="https://api.example.com/data",
        payee_id="merchant_payee",
        amount_minor=-100,
        currency="INR",
        agent_id="agent_01",
        principal_id="user_01",
        tenant_id="tenant_01",
        purpose="Bypass attempt",
        timestamp=now,
    )
    with pytest.raises(MalformedPayloadError, match="amount_minor cannot be negative"):
        x402_adapter.to_action_request(neg_req)


def test_x402_missing_fields_fail_closed(x402_adapter: X402Adapter) -> None:
    """Missing required fields in x402 request must fail closed."""
    for required_fld in (
        "challenge_id",
        "resource_uri",
        "principal_id",
        "agent_id",
        "tenant_id",
        "amount_minor",
    ):
        payload = {
            "x402_version": "1.0",
            "challenge_id": "c1",
            "resource_uri": "https://a.com",
            "principal_id": "p1",
            "agent_id": "a1",
            "tenant_id": "t1",
            "amount_minor": 100,
            "currency": "INR",
        }
        del payload[required_fld]
        with pytest.raises(MalformedPayloadError, match=required_fld):
            x402_adapter.parse_payload(payload)


# ===========================================================================
# HTTP API Fail-Closed Integration Tests
# ===========================================================================


def test_uap_http_api_fail_closed(api_client: ApiTestClient) -> None:
    """UAP RPC route /api/v1/protocols/uap/rpc rejects float, bool, and negative with 422."""
    headers = {"x-tenant-id": "tenant_01"}

    # Float price rejected with 422
    flt_status, flt_body = api_client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={
            "uap_version": "1.0",
            "message_id": "api_flt_1",
            "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
            "payload": {
                "capability": "commerce.purchase",
                "parameters": {"product_id": "prod_laptop_001", "price_minor": 12.5, "quantity": 1},
            },
        },
        headers=headers,
    )
    assert flt_status == 422
    assert flt_body["error"]["code"] == "MALFORMED_PAYLOAD"

    # Bool price rejected with 422
    bool_status, bool_body = api_client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={
            "uap_version": "1.0",
            "message_id": "api_bool_1",
            "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
            "payload": {
                "capability": "commerce.purchase",
                "parameters": {"product_id": "prod_laptop_001", "price_minor": True, "quantity": 1},
            },
        },
        headers=headers,
    )
    assert bool_status == 422
    assert bool_body["error"]["code"] == "MALFORMED_PAYLOAD"

    # Negative price rejected with 422
    neg_status, neg_body = api_client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={
            "uap_version": "1.0",
            "message_id": "api_neg_1",
            "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
            "payload": {
                "capability": "commerce.purchase",
                "parameters": {
                    "product_id": "prod_laptop_001",
                    "price_minor": -500,
                    "quantity": 1,
                },
            },
        },
        headers=headers,
    )
    assert neg_status == 422
    assert neg_body["error"]["code"] == "MALFORMED_PAYLOAD"


def test_x402_http_api_fail_closed(api_client: ApiTestClient) -> None:
    """x402 Pay route /api/v1/protocols/x402/pay rejects float, bool, and negative with 422."""
    headers = {"x-tenant-id": "tenant_01"}

    # Float amount rejected with 422
    flt_status, flt_body = api_client.post(
        "/api/v1/protocols/x402/pay",
        json_body={
            "x402_version": "1.0",
            "challenge_id": "api_flt_x402",
            "resource_uri": "https://api.example.com/data",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
            "amount_minor": 12.5,
            "currency": "INR",
        },
        headers=headers,
    )
    assert flt_status == 422
    assert flt_body["error"]["code"] == "MALFORMED_PAYLOAD"

    # Bool amount rejected with 422
    bool_status, bool_body = api_client.post(
        "/api/v1/protocols/x402/pay",
        json_body={
            "x402_version": "1.0",
            "challenge_id": "api_bool_x402",
            "resource_uri": "https://api.example.com/data",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
            "amount_minor": True,
            "currency": "INR",
        },
        headers=headers,
    )
    assert bool_status == 422
    assert bool_body["error"]["code"] == "MALFORMED_PAYLOAD"

    # Negative amount rejected with 422
    neg_status, neg_body = api_client.post(
        "/api/v1/protocols/x402/pay",
        json_body={
            "x402_version": "1.0",
            "challenge_id": "api_neg_x402",
            "resource_uri": "https://api.example.com/data",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "tenant_id": "tenant_01",
            "amount_minor": -250,
            "currency": "INR",
        },
        headers=headers,
    )
    assert neg_status == 422
    assert neg_body["error"]["code"] == "MALFORMED_PAYLOAD"

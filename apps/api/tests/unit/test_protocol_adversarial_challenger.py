"""Empirical adversarial and boundary stress tests for UAP and x402 protocol adapters.

Challenger 2 verification suite for Milestone 1:
- Invalid JSON-RPC versions ("1.0", null, missing, unsupported)
- Invalid or unsupported capability formats (dot-delimiters, whitespace, missing, injection)
- Corrupted x402 challenge strings, negative micropayment amounts, malformed URIs
- Unauthorized, non-existent, expired, suspended, and mismatched agent URNs
- Fail-closed behavior: standard error envelopes (400, 403, 422, 503), zero 500 crashes, policy gating
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.protocols import (
    MalformedPayloadError,
    UAPAdapter,
    UnsupportedActionError,
    UnsupportedProtocolVersionError,
    X402Adapter,
    X402Challenge,
)
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.errors import InvalidActionRequestError
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
)
from agentpay.domain.policy import SpendingLimits


@pytest.fixture
def api_fixture() -> tuple[ApiTestClient, AgentPayApiApp, Any]:
    orch, _tenant_repo, agent_repo, perms_repo, *_ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    now = datetime.now(UTC)
    active_agent = Agent(
        agent_id="agent_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        scopes=("commerce:read", "commerce:write"),
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save("tenant_01", active_agent)

    # Seed a suspended agent
    suspended_agent = Agent(
        agent_id="agent_suspended",
        tenant_id="tenant_01",
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("payment.request",),
        scopes=("commerce:read",),
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        status=AgentStatus.SUSPENDED,
    )
    agent_repo.save("tenant_01", suspended_agent)

    # Seed a revoked agent
    revoked_agent = Agent(
        agent_id="agent_revoked",
        tenant_id="tenant_01",
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("payment.request",),
        scopes=("commerce:read",),
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        status=AgentStatus.REVOKED,
    )
    agent_repo.save("tenant_01", revoked_agent)

    # Seed an expired agent
    expired_agent = Agent(
        agent_id="agent_expired",
        tenant_id="tenant_01",
        principal_id="user_01",
        issuer="https://auth.agentpay.local",
        capabilities=("payment.request",),
        scopes=("commerce:read",),
        created_at=now - timedelta(days=30),
        expires_at=now - timedelta(days=1),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save("tenant_01", expired_agent)

    # Seed an agent on a foreign tenant (tenant_02)
    foreign_agent = Agent(
        agent_id="agent_foreign",
        tenant_id="tenant_02",
        principal_id="user_foreign",
        issuer="https://auth.agentpay.local",
        capabilities=("payment.request",),
        scopes=("commerce:read",),
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save("tenant_02", foreign_agent)

    perms = AgentPermissions(
        agent_id="agent_01",
        tenant_id="tenant_01",
        capabilities=("catalog.search", "payment.request", "checkout.prepare"),
        limits=SpendingLimits(
            per_transaction_minor=200000,
            daily_minor=500000,
            requires_approval_above_minor=200000,
        ),
        allowed_categories=("electronics", "computers", "web"),
        requires_approval_above_minor=200000,
    )
    perms_repo.save("tenant_01", perms)

    return client, app, orch


# ===========================================================================
# 1. Adversarial Tests: UAP JSON-RPC Versions
# ===========================================================================


@pytest.mark.parametrize(
    "bad_version",
    [
        "3.0",
        "0.9",
        "rpc-1.0",
        "beta",
        "",
        "None",
    ],
)
def test_uap_adapter_rejects_invalid_jsonrpc_versions_direct(bad_version: str) -> None:
    adapter = UAPAdapter()
    raw = {
        "jsonrpc": bad_version,
        "id": "rpc_001",
        "sender": "agent_01",
        "tenant": "tenant_01",
        "principal": "user_01",
        "method": "commerce.purchase",
        "params": {"product_id": "prod_01", "price_minor": 100},
    }
    with pytest.raises(UnsupportedProtocolVersionError) as exc_info:
        adapter.parse_payload(raw)
    assert exc_info.value.code == "UNSUPPORTED_PROTOCOL_VERSION"


def test_uap_adapter_null_and_missing_version_direct() -> None:
    adapter = UAPAdapter()

    # Missing version field
    with pytest.raises(UnsupportedProtocolVersionError):
        adapter.parse_payload(
            {
                "id": "rpc_001",
                "sender": "agent_01",
                "tenant": "tenant_01",
                "principal": "user_01",
                "method": "commerce.purchase",
                "params": {"product_id": "prod_01"},
            }
        )

    # Explicit null version
    with pytest.raises(UnsupportedProtocolVersionError):
        adapter.parse_payload(
            {
                "jsonrpc": None,
                "id": "rpc_001",
                "sender": "agent_01",
                "tenant": "tenant_01",
                "principal": "user_01",
                "method": "commerce.purchase",
                "params": {"product_id": "prod_01"},
            }
        )


def test_uap_adapter_jsonrpc_1_0_acceptance_finding() -> None:
    """Documents finding: UAP adapter accepts jsonrpc: '1.0' because '1.0' is in SUPPORTED_UAP_VERSIONS."""
    adapter = UAPAdapter()
    msg = adapter.parse_payload(
        {
            "jsonrpc": "1.0",
            "id": "rpc_v1",
            "sender": "agent_01",
            "tenant": "tenant_01",
            "principal": "user_01",
            "method": "commerce.purchase",
            "params": {"product_id": "prod_01", "price_minor": 100},
        }
    )
    # The adapter accepts "1.0" even when labeled under jsonrpc key
    assert msg.uap_version == "1.0"


def test_api_uap_rpc_invalid_and_missing_versions_fail_closed(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    # Null jsonrpc
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={"jsonrpc": None, "id": "req_01", "header": {"tenant": "tenant_01"}},
        headers=headers,
    )
    assert status == 400
    assert body["error"]["code"] == "UNSUPPORTED_PROTOCOL_VERSION"

    # Missing jsonrpc / version
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={"id": "req_02", "header": {"tenant": "tenant_01"}},
        headers=headers,
    )
    assert status == 400
    assert body["error"]["code"] == "UNSUPPORTED_PROTOCOL_VERSION"

    # Arbitrary unsupported version string
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body={
            "jsonrpc": "99.0",
            "id": "req_03",
            "sender": "agent_01",
            "tenant": "tenant_01",
            "principal": "user_01",
            "method": "commerce.purchase",
            "params": {"product_id": "prod_01"},
        },
        headers=headers,
    )
    assert status == 400
    assert body["error"]["code"] == "UNSUPPORTED_PROTOCOL_VERSION"


# ===========================================================================
# 2. Adversarial Tests: UAP Capability Formats
# ===========================================================================


@pytest.mark.parametrize(
    "bad_cap",
    [
        "singleword",
        "commerce.",
        ".purchase",
        "commerce..purchase",
        "commerce.purchase.item",
        "commerce/purchase",
        ":::",
        ".",
        "a.b.c.d",
    ],
)
def test_uap_adapter_rejects_malformed_capability_formats_direct(bad_cap: str) -> None:
    adapter = UAPAdapter()
    raw = {
        "jsonrpc": "2.0",
        "id": "rpc_cap_001",
        "sender": "agent_01",
        "tenant": "tenant_01",
        "principal": "user_01",
        "method": bad_cap,
        "params": {"product_id": "prod_01", "price_minor": 100},
    }
    msg = adapter.parse_payload(raw)
    with pytest.raises(UnsupportedActionError) as exc_info:
        adapter.to_action_request(msg)
    assert exc_info.value.code == "UNSUPPORTED_ACTION"


def test_uap_adapter_whitespace_capability_fails_closed_at_domain() -> None:
    """Whitespace-only capability '   .   ' fails closed with InvalidActionRequestError."""
    adapter = UAPAdapter()
    raw = {
        "jsonrpc": "2.0",
        "id": "rpc_cap_ws",
        "sender": "agent_01",
        "tenant": "tenant_01",
        "principal": "user_01",
        "method": "   .   ",
        "params": {"product_id": "prod_01", "price_minor": 100},
    }
    msg = adapter.parse_payload(raw)
    with pytest.raises(InvalidActionRequestError, match="must be a non-empty string"):
        adapter.to_action_request(msg)


def test_api_uap_rpc_malformed_capability_returns_standard_error(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_bad_cap",
        "header": {
            "sender": "urn:agentpay:agent:agent_01",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "malformed_no_dot",
            "parameters": {"product_id": "prod_01", "price_minor": 100},
        },
    }
    status, body = client.post("/api/v1/protocols/uap/rpc", json_body=payload, headers=headers)
    assert status == 400
    assert body["error"]["code"] == "UNSUPPORTED_ACTION"
    assert "error" in body
    assert "request_id" in body["error"]


def test_api_uap_rpc_whitespace_capability_returns_422(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "jsonrpc": "2.0",
        "id": "rpc_ws_cap",
        "sender": "agent_01",
        "tenant": "tenant_01",
        "principal": "user_01",
        "method": "   .   ",
        "params": {"product_id": "prod_01", "price_minor": 100},
    }
    status, body = client.post("/api/v1/protocols/uap/rpc", json_body=payload, headers=headers)
    assert status == 422
    assert body["error"]["code"] == "INVALID_ACTION_REQUEST"


# ===========================================================================
# 3. Adversarial Tests: x402 Challenges, Negative Amounts & Malformed URIs
# ===========================================================================


def test_x402_challenge_header_formatting_and_escaping() -> None:
    chal = X402Challenge(
        challenge_id="chal_spec!#$",
        resource_uri="https://api.domain.com/v1/stream?query=1&limit=10",
        amount_minor=5000,
        currency="INR",
        payee_id="merchant_99",
    )
    header = chal.to_header()
    assert 'challenge_id="chal_spec!#$"' in header
    assert 'resource="https://api.domain.com/v1/stream?query=1&limit=10"' in header
    assert "amount=5000" in header
    assert 'currency="INR"' in header
    assert 'payee="merchant_99"' in header


@pytest.mark.parametrize(
    "bad_amount",
    [
        -1,
        -999999,
        "not_a_number",
        "-50",
    ],
)
def test_x402_adapter_rejects_negative_and_invalid_amounts_direct(bad_amount: Any) -> None:
    adapter = X402Adapter()
    raw = {
        "x402_version": "1.0",
        "challenge_id": "chal_01",
        "resource_uri": "https://api.example.com/data",
        "amount_minor": bad_amount,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
    }
    with pytest.raises(MalformedPayloadError) as exc_info:
        adapter.parse_payload(raw)
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


def test_api_x402_rejects_negative_amounts(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "x402_version": "1.0",
        "challenge_id": "chal_neg",
        "resource_uri": "https://data.com/stream",
        "amount_minor": -500,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
    }
    status, body = client.post("/api/v1/protocols/x402/pay", json_body=payload, headers=headers)
    assert status == 422
    assert body["error"]["code"] == "MALFORMED_PAYLOAD"
    assert "amount_minor" in body["error"]["message"].lower()


@pytest.mark.parametrize(
    "missing_fld",
    [
        "challenge_id",
        "resource_uri",
        "principal_id",
        "agent_id",
        "tenant_id",
        "amount_minor",
    ],
)
def test_x402_adapter_missing_required_fields_fails_closed(missing_fld: str) -> None:
    adapter = X402Adapter()
    raw: dict[str, Any] = {
        "x402_version": "1.0",
        "challenge_id": "chal_01",
        "resource_uri": "https://api.example.com/data",
        "amount_minor": 100,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
    }
    del raw[missing_fld]
    with pytest.raises(MalformedPayloadError):
        adapter.parse_payload(raw)


def test_api_x402_missing_resource_uri_returns_422(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "x402_version": "1.0",
        "challenge_id": "chal_no_uri",
        "amount_minor": 100,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
    }
    status, body = client.post("/api/v1/protocols/x402/pay", json_body=payload, headers=headers)
    assert status == 422
    assert body["error"]["code"] == "MALFORMED_PAYLOAD"


def test_x402_null_challenge_and_uri_stringification_finding() -> None:
    """Documents finding: raw_data.get('challenge_id') returning None becomes string 'None'."""
    adapter = X402Adapter()
    req = adapter.parse_payload(
        {
            "x402_version": "1.0",
            "challenge_id": None,
            "resource_uri": None,
            "amount_minor": 100,
            "agent_id": "agent_01",
            "principal_id": "user_01",
            "tenant_id": "tenant_01",
        }
    )
    # Finding: str(None) converts None into string 'None' rather than raising MalformedPayloadError
    assert req.challenge_id == "None"
    assert req.resource_uri == "None"


# ===========================================================================
# 4. Adversarial Tests: Agent Identity, URNs, Statuses & Cross-Tenant Access
# ===========================================================================


def test_uap_urn_parsing_variations() -> None:
    adapter = UAPAdapter()

    # Standard URN: urn:agentpay:agent:agent_01 -> extracted as agent_01
    raw1 = {
        "uap_version": "1.0",
        "message_id": "m1",
        "header": {
            "sender": "urn:agentpay:agent:agent_01",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "price_minor": 100},
        },
    }
    req1 = adapter.parse_payload(raw1)
    act1, _ = adapter.to_action_request(req1)
    assert act1.agent_id == "agent_01"

    # Raw agent_id without URN prefix
    raw2 = {
        **raw1,
        "header": {
            "sender": "agent_02",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
    }
    req2 = adapter.parse_payload(raw2)
    act2, _ = adapter.to_action_request(req2)
    assert act2.agent_id == "agent_02"


def test_api_cross_tenant_header_payload_mismatch_denied(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture

    # UAP: header tenant_01, body header tenant_02
    uap_body = {
        "uap_version": "1.0",
        "message_id": "uap_ct_1",
        "header": {
            "sender": "urn:agentpay:agent:agent_01",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_02",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "price_minor": 100},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc", json_body=uap_body, headers={"x-tenant-id": "tenant_01"}
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"

    # x402: header tenant_01, body tenant_id tenant_02
    x402_body = {
        "x402_version": "1.0",
        "challenge_id": "chal_ct_1",
        "resource_uri": "https://api.market.com/feed",
        "amount_minor": 100,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_02",
    }
    status, body = client.post(
        "/api/v1/protocols/x402/pay", json_body=x402_body, headers={"x-tenant-id": "tenant_01"}
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"


def test_api_uap_revoked_and_suspended_agents_fail_closed(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    # Revoked agent invocation
    revoked_payload = {
        "uap_version": "1.0",
        "message_id": "msg_revoked",
        "header": {
            "sender": "urn:agentpay:agent:agent_revoked",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "variant_id": "var_01", "price_minor": 1000},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc", json_body=revoked_payload, headers=headers
    )
    assert status == 200  # Protocol envelope returns error result
    assert body["status"] == "REJECTED"
    assert body["error"] is not None
    assert "revoked" in body["error"]["message"].lower()

    # Suspended agent invocation
    suspended_payload = {
        "uap_version": "1.0",
        "message_id": "msg_suspended",
        "header": {
            "sender": "urn:agentpay:agent:agent_suspended",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "variant_id": "var_01", "price_minor": 1000},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc", json_body=suspended_payload, headers=headers
    )
    assert status == 200
    assert body["status"] == "REJECTED"
    assert body["error"] is not None
    assert "suspended" in body["error"]["message"].lower()


def test_api_uap_expired_agent_fails_closed(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    expired_payload = {
        "uap_version": "1.0",
        "message_id": "msg_expired",
        "header": {
            "sender": "urn:agentpay:agent:agent_expired",
            "recipient": "urn:agentpay:service:control_plane",
            "tenant": "tenant_01",
            "principal": "user_01",
        },
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_01", "variant_id": "var_01", "price_minor": 1000},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc", json_body=expired_payload, headers=headers
    )
    assert status == 200
    assert body["status"] == "REJECTED"
    assert body["error"] is not None
    assert "expired" in body["error"]["message"].lower()


def test_unregistered_and_foreign_agent_bypass_finding(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    """Documents finding: Unregistered ghost agent and foreign tenant agent bypass authorize_action.

    Because execute_purchase checks:
        if agent is not None:
            self._auth_service.authorize_action(...)
    when an agent is not found in the tenant's agent_repo, agent is None, and authorization
    is skipped, allowing transactions to proceed under default policy limits!
    """
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    # 1. Ghost/unregistered agent in x402
    x402_ghost = {
        "x402_version": "1.0",
        "challenge_id": "chal_ghost_test",
        "resource_uri": "https://api.market.com/stream",
        "amount_minor": 1000,
        "agent_id": "ghost_unregistered_agent",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
        "purpose": "unregistered access",
    }
    status, body = client.post("/api/v1/protocols/x402/pay", json_body=x402_ghost, headers=headers)
    assert status == 200
    # Verifies that the transaction fails closed when the agent is unregistered (§177 Vector 1 & 4)
    assert body["status"] == "FAILED"
    assert body["authorization_token"] is None

    # 2. Foreign tenant agent in x402
    x402_foreign = {
        "x402_version": "1.0",
        "challenge_id": "chal_foreign_test",
        "resource_uri": "https://api.market.com/stream",
        "amount_minor": 1000,
        "agent_id": "agent_foreign",  # Exists only on tenant_02
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
        "purpose": "foreign agent access",
    }
    status_f, body_f = client.post(
        "/api/v1/protocols/x402/pay", json_body=x402_foreign, headers=headers
    )
    assert status_f == 200
    assert body_f["status"] == "FAILED"
    assert body_f["authorization_token"] is None


# ===========================================================================
# 5. Adversarial Tests: Spending Policy Enforcement Across Protocols
# ===========================================================================


def test_api_x402_spending_ceiling_policy_rejection_fails_closed(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    """Verifies that an x402 payment request exceeding spending limits is rejected without issuing a token."""
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    # Attempt x402 payment of ₹30,000 (3,000,000 minor units), well above the ₹20,000 ceiling
    payload = {
        "x402_version": "1.0",
        "challenge_id": "chal_over_ceiling",
        "resource_uri": "https://api.market.com/expensive-enterprise-data",
        "amount_minor": 3000000,
        "agent_id": "agent_01",
        "principal_id": "user_01",
        "tenant_id": "tenant_01",
    }
    status, body = client.post("/api/v1/protocols/x402/pay", json_body=payload, headers=headers)
    assert status == 200
    assert body["status"] in ("FAILED", "AWAITING_APPROVAL")
    # Crucial security assertion: Settlement token MUST NOT be issued
    assert body["authorization_token"] is None
    assert body["settlement_ref"] is None


def test_api_uap_spending_ceiling_rejection_fails_closed(
    api_fixture: tuple[ApiTestClient, AgentPayApiApp, Any],
) -> None:
    client, _, _ = api_fixture
    headers = {"x-tenant-id": "tenant_01"}

    # Request exceeding limit
    payload = {
        "uap_version": "1.0",
        "message_id": "msg_excessive_spending",
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
                "price_minor": 3000000,
                "currency": "INR",
            },
        },
    }
    status, body = client.post("/api/v1/protocols/uap/rpc", json_body=payload, headers=headers)
    assert status == 200
    assert body["status"] == "REJECTED" or body["result"]["decision"] in ("REVIEW", "DENY")
    assert body["result"]["order_id"] is None

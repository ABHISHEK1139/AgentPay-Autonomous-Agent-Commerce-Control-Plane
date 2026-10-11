"""Integration tests for API rate limiting, request throttling, and HTTP 429 response handling (§52, §62).

Validates:
- Requests under the rate limit succeed normally (200 OK) with rate limit headers.
- High-volume bursting exceeding capacity triggers HTTP 429 RATE_LIMIT_EXCEEDED.
- Standard RFC 6585/7231 error envelope with code, message, details (retry_after_seconds, limit, remaining, scope).
- RFC Retry-After and X-RateLimit-* headers in response (both API client and ASGI responses).
- Dual-tier throttling: agent bucket vs tenant bucket exhaustion.
- Independent isolation across tenants and agents.
- Read-only endpoints (catalog) have separate higher thresholds and bypass tight mutating limits.
- Health and OpenAPI public discovery endpoints bypass rate limiting entirely.
- Time advancement refills tokens and allows subsequent requests to succeed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.rate_limiter import InMemoryRateLimiter
from agentpay.api.app import AgentPayApiApp, ApiTestClient


def _action_payload(action_id: str, agent_id: str = "agent_01") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "principal_id": "user_01",
        "agent_id": agent_id,
        "domain": "commerce",
        "action": "purchase",
        "resource_type": "product",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 129900,
        "currency": "INR",
        "intent_purpose": "test purchase",
        "quantity": 1,
    }


def test_under_limit_actions_succeed_with_rate_limit_headers() -> None:
    """Requests within the token bucket capacity succeed with 200 and include rate limit headers."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, mutating_agent_capacity=5)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    status, body = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_under_1"),
        headers=headers,
    )
    assert status == 200
    assert body["status"] == "COMPLETED"

    # Verify rate limit headers
    resp_headers = client.last_response_headers
    assert "x-ratelimit-limit" in resp_headers
    assert resp_headers["x-ratelimit-limit"] == "5"
    assert "x-ratelimit-remaining" in resp_headers
    assert int(resp_headers["x-ratelimit-remaining"]) == 4


def test_high_volume_bursting_triggers_http_429() -> None:
    """Bursting beyond agent bucket capacity triggers HTTP 429 RATE_LIMIT_EXCEEDED."""
    orch, *_ = setup_orchestrator()
    # Configure tight capacity of 3 requests for mutating endpoints
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=3,
        mutating_agent_refill_rate=1.0,
    )
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # 3 requests succeed
    for i in range(3):
        status, _ = client.post(
            "/api/v1/actions",
            json_body=_action_payload(f"act_burst_{i}"),
            headers=headers,
        )
        assert status == 200

    # 4th request must fail with 429
    status, body = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_burst_blocked"),
        headers=headers,
    )
    assert status == 429
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Rate limit exceeded" in body["error"]["message"]
    assert "request_id" in body["error"]

    # Verify details
    details = body["error"]["details"]
    assert details["limit"] == 3
    assert details["remaining"] == 0
    assert details["scope"] == "agent"
    assert details["retry_after_seconds"] > 0

    # Verify RFC Retry-After header
    resp_headers = client.last_response_headers
    assert "retry-after" in resp_headers
    retry_after = int(resp_headers["retry-after"])
    assert retry_after >= 1


def test_dual_tier_tenant_exhaustion_triggers_tenant_scope_429() -> None:
    """When the aggregate tenant capacity is exhausted across multiple agents, 429 has scope='tenant'."""
    orch, *_ = setup_orchestrator()
    # Agent capacity is 5, but overall tenant capacity is only 2
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=5,
        mutating_tenant_capacity=2,
        mutating_tenant_refill_rate=0.5,
    )
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # Agent 1 uses 1 token
    status, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_t1", agent_id="agent_alpha"),
        headers=headers,
    )
    assert status == 200

    # Agent 2 uses 1 token (tenant bucket at 0)
    status, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_t2", agent_id="agent_beta"),
        headers=headers,
    )
    assert status == 200

    # Agent 3 attempts request: agent has budget, but tenant is exhausted
    status, body = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_t3", agent_id="agent_gamma"),
        headers=headers,
    )
    assert status == 429
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert body["error"]["details"]["scope"] == "tenant"
    assert body["error"]["details"]["limit"] == 2
    assert body["error"]["details"]["remaining"] == 0


def test_independent_tenant_isolation() -> None:
    """Exhausting tenant_01 rate limit does not affect tenant_02."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=2,
        mutating_tenant_capacity=2,
    )
    client = ApiTestClient(app).as_buyer()

    # Exhaust tenant_01
    for i in range(2):
        status, _ = client.post(
            "/api/v1/actions",
            json_body=_action_payload(f"act_t1_{i}"),
            headers={"x-tenant-id": "tenant_01"},
        )
        assert status == 200

    status_blocked, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_t1_over"),
        headers={"x-tenant-id": "tenant_01"},
    )
    assert status_blocked == 429

    # tenant_02 is completely unaffected
    token_t2 = client.create_token(tenant_id="tenant_02", principal_id="usr_t2")
    status_t2, _body_t2 = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_t2_1"),
        headers={"x-tenant-id": "tenant_02", "authorization": f"Bearer {token_t2}"},
    )
    assert status_t2 == 200


def test_independent_agent_isolation_within_same_tenant() -> None:
    """Agent A exhausting its limit does not immediately block Agent B within the same tenant."""
    orch, *_ = setup_orchestrator()
    # Agent limit = 2, Tenant limit = 10
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=2,
        mutating_tenant_capacity=10,
    )
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # Agent A exhausts its 2 tokens
    for i in range(2):
        status, _ = client.post(
            "/api/v1/actions",
            json_body=_action_payload(f"act_a_{i}", agent_id="agent_a"),
            headers=headers,
        )
        assert status == 200

    # Agent A is blocked
    status_a_blocked, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_a_over", agent_id="agent_a"),
        headers=headers,
    )
    assert status_a_blocked == 429

    # Agent B has independent tokens and succeeds
    status_b, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_b_1", agent_id="agent_b"),
        headers=headers,
    )
    assert status_b == 200


def test_protocol_adapters_rate_limiting() -> None:
    """Protocol endpoints (ACP, AP2, UAP, x402) are subject to mutating rate limits."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, mutating_agent_capacity=2)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    acp_payload = {
        "acp_version": "1.0",
        "request_id": "acp_req_01",
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

    # Request 1 & 2 succeed
    status_1, _ = client.post(
        "/api/v1/protocols/acp/checkout", json_body=acp_payload, headers=headers
    )
    assert status_1 == 200

    status_2, _ = client.post(
        "/api/v1/protocols/acp/checkout", json_body=acp_payload, headers=headers
    )
    assert status_2 == 200

    # Request 3 triggers 429
    status_3, body_3 = client.post(
        "/api/v1/protocols/acp/checkout", json_body=acp_payload, headers=headers
    )
    assert status_3 == 429
    assert body_3["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert client.last_response_headers.get("retry-after") is not None


def test_tool_execution_rate_limiting() -> None:
    """Tool invocation endpoints are throttled under mutating route limits."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, mutating_agent_capacity=2)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    tool_payload = {"agent_id": "agent_01", "parameters": {"query": "laptop"}}

    # Invocations 1 & 2 succeed
    s1, _ = client.post(
        "/api/v1/tools/search_products/invoke", json_body=tool_payload, headers=headers
    )
    assert s1 == 200
    s2, _ = client.post(
        "/api/v1/tools/search_products/invoke", json_body=tool_payload, headers=headers
    )
    assert s2 == 200

    # Invocation 3 is throttled
    s3, body_3 = client.post(
        "/api/v1/tools/search_products/invoke", json_body=tool_payload, headers=headers
    )
    assert s3 == 429
    assert body_3["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_read_only_catalog_endpoints_bypass_mutating_limits() -> None:
    """Exhausting the mutating financial action limit does not block read-only catalog browsing."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=1,
        read_agent_capacity=50,
    )
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # Exhaust mutating action limit
    s_act, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_single"),
        headers=headers,
    )
    assert s_act == 200

    # Mutating is now blocked
    s_act_blocked, _ = client.post(
        "/api/v1/actions",
        json_body=_action_payload("act_over"),
        headers=headers,
    )
    assert s_act_blocked == 429

    # Catalog endpoints remain accessible with 200 OK
    s_cat, body_cat = client.get("/agent/catalog", headers=headers)
    assert s_cat == 200
    assert "products" in body_cat

    s_prod, body_prod = client.get("/agent/products/prod_01", headers=headers)
    assert s_prod == 200
    assert body_prod["name"] == "Pro Laptop"


def test_health_and_openapi_endpoints_bypass_rate_limiting() -> None:
    """Public discovery endpoints (/api/v1/health, /openapi.json) bypass throttling completely."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(
        orchestrator=orch,
        mutating_agent_capacity=0,  # zero capacity
        read_agent_capacity=0,
    )
    client = ApiTestClient(app).as_buyer()

    status_h, body_h = client.get("/api/v1/health", auth=False)
    assert status_h == 200
    assert body_h["status"] == "healthy"

    status_o, body_o = client.get("/openapi.json", auth=False)
    assert status_o == 200
    assert body_o["openapi"] == "3.1.0"


def test_rate_limit_refill_recovery() -> None:
    """After exhausting the limit, advancing the clock allows subsequent requests to succeed."""
    current_time = 1000.0
    limiter = InMemoryRateLimiter(time_func=lambda: current_time)

    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(
        orchestrator=orch,
        rate_limiter=limiter,
        mutating_agent_capacity=2,
        mutating_agent_refill_rate=1.0,  # 1 token per second
    )
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # Use 2 tokens
    assert (
        client.post("/api/v1/actions", json_body=_action_payload("act_r1"), headers=headers)[0]
        == 200
    )
    assert (
        client.post("/api/v1/actions", json_body=_action_payload("act_r2"), headers=headers)[0]
        == 200
    )

    # 3rd is blocked
    assert (
        client.post("/api/v1/actions", json_body=_action_payload("act_r3"), headers=headers)[0]
        == 429
    )

    # Advance clock by 1.5 seconds -> 1.5 tokens refilled
    current_time += 1.5

    # Request now succeeds!
    status_recovered, _ = client.post(
        "/api/v1/actions", json_body=_action_payload("act_r4"), headers=headers
    )
    assert status_recovered == 200


def test_handle_request_with_headers_tuple_return() -> None:
    """handle_request_with_headers returns a 3-tuple (status_code, body, headers)."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, mutating_agent_capacity=1)
    client = ApiTestClient(app).as_buyer()
    token = client.get_or_create_token("tenant_01")
    headers = {"x-tenant-id": "tenant_01", "authorization": f"Bearer {token}"}

    # 1st request succeeds
    status, _body, resp_hdrs = app.handle_request_with_headers(
        "POST", "/api/v1/actions", headers=headers, body=_action_payload("act_tup_1")
    )
    assert status == 200
    assert "x-ratelimit-limit" in resp_hdrs

    # 2nd request fails with 429
    status_429, body_429, resp_hdrs_429 = app.handle_request_with_headers(
        "POST", "/api/v1/actions", headers=headers, body=_action_payload("act_tup_2")
    )
    assert status_429 == 429
    assert body_429["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "retry-after" in resp_hdrs_429


def test_asgi_429_response_headers() -> None:
    """Raw ASGI dispatch emits [b'retry-after', ...] in http.response.start headers on 429."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, mutating_agent_capacity=1)
    client = ApiTestClient(app).as_buyer()
    token = client.get_or_create_token("tenant_01")

    async def call_asgi(payload: dict[str, Any]) -> tuple[int, list[list[bytes]], dict[str, Any]]:
        sent_messages: list[dict[str, Any]] = []

        async def mock_receive() -> dict[str, Any]:
            body_bytes = json.dumps(payload).encode("utf-8")
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        async def mock_send(message: dict[str, Any]) -> None:
            sent_messages.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/actions",
            "headers": [
                [b"authorization", f"Bearer {token}".encode("latin1")],
                [b"x-tenant-id", b"tenant_01"],
                [b"content-type", b"application/json"],
            ],
        }

        await app(scope, mock_receive, mock_send)
        start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
        body_msg = next(m for m in sent_messages if m["type"] == "http.response.body")
        return (
            start_msg["status"],
            start_msg["headers"],
            json.loads(body_msg["body"].decode("utf-8")),
        )

    # First call: 200
    status_1, _hdrs_1, _ = asyncio.run(call_asgi(_action_payload("act_asgi_1")))
    assert status_1 == 200

    # Second call: 429 with retry-after header
    status_2, hdrs_2, body_2 = asyncio.run(call_asgi(_action_payload("act_asgi_2")))
    assert status_2 == 429
    assert body_2["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    # Find retry-after in raw headers
    header_dict = {k.decode("latin1").lower(): v.decode("latin1") for k, v in hdrs_2}
    assert "retry-after" in header_dict
    assert int(header_dict["retry-after"]) >= 1

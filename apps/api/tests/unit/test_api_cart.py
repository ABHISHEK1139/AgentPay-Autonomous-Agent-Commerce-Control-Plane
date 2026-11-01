"""HTTP API tests for the agent-facing shopping cart endpoints (§13, §38, ADR-0006).

Validates:
- Cart create/add/view/remove endpoints with strict tenant + agent binding.
- Checkout execution from the cart path (ALLOW and REVIEW-to-approval flows).
- Failure envelopes: 401 unauthenticated, 403 impersonation, 404 unknown cart,
  503 emergency pause, and 200 semantic FAILED results for policy conflicts.
"""

from __future__ import annotations

from test_cart_flow import setup_cart_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient

TENANT = "tenant_01"
HEADERS = {"x-tenant-id": TENANT}


def make_client() -> tuple[ApiTestClient, AgentPayApiApp]:
    orch, _, _ = setup_cart_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    return ApiTestClient(app), app


def create_agent_cart(client: ApiTestClient) -> str:
    status, body = client.as_agent("agent_01").post("/agent/carts", json_body={}, headers=HEADERS)
    assert status == 201
    assert body["status"] == "ACTIVE"
    assert body["merchant_id"] is None
    return str(body["cart_id"])


def test_cart_http_lifecycle_allow_checkout() -> None:
    client, app = make_client()
    agent = client.as_agent("agent_01")

    cart_id = create_agent_cart(client)

    status, body = agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01", "quantity": 1},
        headers=HEADERS,
    )
    assert status == 200
    status, body = agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_02", "quantity": 1},
        headers=HEADERS,
    )
    assert status == 200
    assert body["merchant_id"] == "merch_01"
    assert body["total_minor"] == 179900
    assert len(body["items"]) == 2

    status, body = agent.get(f"/agent/carts/{cart_id}", headers=HEADERS)
    assert status == 200
    assert body["total_minor"] == 179900
    assert all(item["resolved"] for item in body["items"])

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 179900},
        headers=HEADERS,
    )
    assert status == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["cart_status"] == "CHECKED_OUT"
    assert len(body["order_ids"]) == 2
    assert body["payment_id"]

    inventory_repo = app.orchestrator._inventory_repo
    assert inventory_repo is not None
    inv = inventory_repo.get(TENANT, "var_01")
    assert inv is not None and inv.reserved == 1


def test_cart_http_remove_item() -> None:
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)

    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )
    status, body = agent.post(
        f"/agent/carts/{cart_id}/items/remove",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )
    assert status == 200
    assert body["items"] == []

    # Removing a variant that is not present fails closed with 400
    status, body = agent.post(
        f"/agent/carts/{cart_id}/items/remove",
        json_body={"variant_id": "var_02"},
        headers=HEADERS,
    )
    assert status == 400
    assert body["error"]["code"]


def test_cart_http_review_then_admin_approval_executes() -> None:
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    admin = client.as_admin()

    cart_id = create_agent_cart(client)
    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01", "quantity": 2},
        headers=HEADERS,
    )

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 259800},
        headers=HEADERS,
    )
    assert status == 200
    assert body["status"] == "AWAITING_APPROVAL"
    assert body["decision"] == "REVIEW"
    approval_id = body["approval_id"]
    assert approval_id

    status, body = admin.post(
        f"/api/v1/approvals/{approval_id}/grant",
        json_body={"approver_id": "usr_test_admin"},
        headers=HEADERS,
    )
    assert status == 200

    status, body = admin.post(
        f"/api/v1/approvals/{approval_id}/execute",
        json_body={"agent_id": "agent_01"},
        headers=HEADERS,
    )
    assert status == 200
    assert body["status"] == "COMPLETED"
    assert body["cart_id"] == cart_id
    assert len(body["order_ids"]) == 1
    assert body["approval_id"] == approval_id

    # The approval is single-use; a second execute is rejected
    status, body = admin.post(
        f"/api/v1/approvals/{approval_id}/execute",
        json_body={"agent_id": "agent_01"},
        headers=HEADERS,
    )
    assert status == 409
    assert body["error"]["code"] == "APPROVAL_ALREADY_CONSUMED"


def test_cart_http_price_mismatch_returns_semantic_failure() -> None:
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)
    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 999900},
        headers=HEADERS,
    )
    assert status == 200
    assert body["status"] == "FAILED"
    assert body["decision"] == "DENY"
    assert body["rejection_reason"]
    assert body["payment_id"] is None


def test_cart_http_agent_impersonation_denied() -> None:
    client, _ = make_client()
    cart_id = create_agent_cart(client)

    intruder = client.as_agent("agent_02")
    status, body = intruder.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )
    assert status == 403
    assert body["error"]["code"] == "AGENT_IMPERSONATION_DENIED"

    status, body = intruder.get(f"/agent/carts/{cart_id}", headers=HEADERS)
    assert status == 403
    assert body["error"]["code"] == "AGENT_IMPERSONATION_DENIED"

    status, body = intruder.post(
        "/agent/carts",
        json_body={"agent_id": "agent_01"},
        headers=HEADERS,
    )
    assert status == 403
    assert body["error"]["code"] == "AGENT_IMPERSONATION_DENIED"


def test_cart_http_checkout_rejects_agent_binding_mismatch() -> None:
    """Checkout may not switch to an agent other than the cart's bound agent (§20)."""
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)
    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 129900, "agent_id": "agent_02"},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "AGENT_BINDING_MISMATCH"
    assert body["error"]["details"] == {}


def test_cart_http_checkout_rejects_principal_binding_mismatch() -> None:
    """Checkout may not change the paying principal recorded on the cart (§20, §49)."""
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)
    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 129900, "principal_id": "user_02"},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "PRINCIPAL_BINDING_MISMATCH"


def test_cart_http_checkout_accepts_matching_bound_identity() -> None:
    """Re-asserting the cart's own bound identities is allowed and changes nothing."""
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)
    agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )

    status, body = agent.post(
        "/agent/checkout",
        json_body={
            "cart_id": cart_id,
            "amount_minor": 129900,
            "agent_id": "agent_01",
            "principal_id": "user_01",
        },
        headers=HEADERS,
    )
    assert status == 200
    assert body["status"] == "COMPLETED"
    assert body["cart_id"] == cart_id


def test_cart_http_unknown_cart_returns_404() -> None:
    client, _ = make_client()
    agent = client.as_agent("agent_01")

    status, body = agent.get("/agent/carts/cart_missing", headers=HEADERS)
    assert status == 404
    assert body["error"]["code"] == "CART_NOT_FOUND"

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": "cart_missing", "amount_minor": 100},
        headers=HEADERS,
    )
    assert status == 404
    assert body["error"]["code"] == "CART_NOT_FOUND"


def test_cart_http_checkout_blocked_by_emergency_pause() -> None:
    client, _ = make_client()
    cart_id = create_agent_cart(client)
    client.as_agent("agent_01").post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01"},
        headers=HEADERS,
    )

    status, _ = client.as_admin().post(
        "/api/v1/emergency/pause",
        json_body={"reason": "cart checkout pause test"},
        headers=HEADERS,
    )
    assert status == 200

    status, body = client.as_agent("agent_01").post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": 129900},
        headers=HEADERS,
    )
    assert status == 503
    assert body["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"


def test_cart_http_unauthenticated_and_missing_tenant() -> None:
    client, _ = make_client()

    status, body = client.as_unauthenticated().get("/agent/carts/cart_x", headers=HEADERS)
    assert status == 401

    status, body = client.as_agent("agent_01").post("/agent/carts", json_body={})
    assert status == 400
    assert body["error"]["code"] == "MISSING_TENANT_HEADER"


def test_cart_http_invalid_payloads_rejected() -> None:
    client, _ = make_client()
    agent = client.as_agent("agent_01")
    cart_id = create_agent_cart(client)

    status, body = agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "  "},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    status, body = agent.post(
        f"/agent/carts/{cart_id}/items",
        json_body={"variant_id": "var_01", "quantity": 0},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_QUANTITY"

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    status, body = agent.post(
        "/agent/checkout",
        json_body={"cart_id": cart_id, "amount_minor": -5},
        headers=HEADERS,
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_AMOUNT"


def test_cart_openapi_and_discovery_contract_include_cart_surface() -> None:
    """The published contract must expose the cart endpoints (§13, §38)."""
    client, _ = make_client()

    status, body = client.as_unauthenticated().get("/openapi.json")
    assert status == 200
    cart_paths = {
        "/agent/carts",
        "/agent/carts/{cart_id}",
        "/agent/carts/{cart_id}/items",
        "/agent/carts/{cart_id}/items/remove",
        "/agent/checkout",
    }
    assert cart_paths <= set(body["paths"])
    assert body["paths"]["/agent/carts"]["post"]["summary"]
    assert body["paths"]["/agent/carts/{cart_id}"]["get"]["summary"]
    assert body["paths"]["/agent/carts/{cart_id}/items"]["post"]["summary"]
    assert body["paths"]["/agent/carts/{cart_id}/items/remove"]["post"]["summary"]
    assert body["paths"]["/agent/checkout"]["post"]["summary"]

    status, body = client.as_unauthenticated().get("/.well-known/agent-commerce")
    assert status == 200
    commerce = body["commerce"]
    assert commerce["carts"] == "/agent/carts"
    assert commerce["cart_checkout"] == "/agent/checkout"

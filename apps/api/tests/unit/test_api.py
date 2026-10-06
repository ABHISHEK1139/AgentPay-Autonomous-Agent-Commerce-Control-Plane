"""Unit tests for the HTTP API layer, agent commerce routes, and emergency controls (§13, §21, §38, §67)."""

from __future__ import annotations

from test_purchase_flow import setup_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient


def test_api_health_and_openapi() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    status_code, body = client.get("/api/v1/health")
    assert status_code == 200
    assert body["status"] == "healthy"
    assert body["emergency_paused"] is False

    status_code, body = client.get("/openapi.json")
    assert status_code == 200
    assert body["openapi"] == "3.1.0"


def test_missing_tenant_header_returns_standard_error_envelope() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    # Calling tenant-scoped endpoint without X-Tenant-ID
    status_code, body = client.get("/agent/catalog")
    assert status_code == 400
    assert "error" in body
    assert body["error"]["code"] == "MISSING_TENANT_HEADER"
    assert "X-Tenant-ID" in body["error"]["message"]
    assert "request_id" in body["error"]


def test_agent_catalog_and_product_lookup() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)
    headers = {"x-tenant-id": "tenant_01"}

    status_code, body = client.get("/agent/catalog", headers=headers)
    assert status_code == 200
    assert len(body["products"]) == 1
    assert body["products"][0]["product_id"] == "prod_01"

    status_code, body = client.get("/agent/products/prod_01", headers=headers)
    assert status_code == 200
    assert body["name"] == "Pro Laptop"

    status_code, body = client.get("/agent/products/unknown_prod", headers=headers)
    assert status_code == 404
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"

    status_code, body = client.get("/agent/products/var_01/inventory", headers=headers)
    assert status_code == 200
    assert body["effective_available"] == 10


def test_action_execution_autonomous_allow() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)
    headers = {"x-tenant-id": "tenant_01"}

    payload = {
        "action_id": "api_act_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 129900,
        "currency": "INR",
        "intent_purpose": "API purchase test",
    }

    status_code, body = client.post("/api/v1/actions", json_body=payload, headers=headers)
    assert status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["order_id"] is not None
    assert body["payment_id"] is not None


def test_emergency_kill_switch_lifecycle() -> None:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)
    headers = {"x-tenant-id": "tenant_01"}

    # 1. Trigger kill switch pause (§21, §67)
    status_code, body = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "sec_operator", "reason": "Suspected breach"},
        headers=headers,
    )
    assert status_code == 200
    assert body["status"] == "PAUSED"
    assert app.get_emergency_status()["paused"] is True

    # 2. Subsequent autonomous action is blocked immediately (503)
    payload = {
        "action_id": "api_act_blocked",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 129900,
    }
    status_code, body = client.post("/api/v1/actions", json_body=payload, headers=headers)
    assert status_code == 503
    assert body["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"

    # 3. Resume operations
    status_code, body = client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "sec_operator"},
        headers=headers,
    )
    assert status_code == 200
    assert body["status"] == "ACTIVE"
    assert app.get_emergency_status()["paused"] is False

    # 4. Action succeeds now
    status_code, body = client.post("/api/v1/actions", json_body=payload, headers=headers)
    assert status_code == 200
    assert body["status"] == "COMPLETED"


def test_approval_flow_via_http() -> None:
    orch, *_, variant_repo, inv_repo, _, _, _approval_repo = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)
    headers = {"x-tenant-id": "tenant_01"}

    from agentpay.domain.commerce import Inventory, ProductVariant
    from agentpay.domain.money import Money

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_expensive",
            product_id="prod_01",
            sku="SKU-EXPENSIVE",
            name="Max Server",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_expensive",
            tenant_id="tenant_01",
            available=5,
            reserved=0,
        ),
    )

    # 1. Action exceeds autonomous limit -> REVIEW -> AWAITING_APPROVAL
    payload = {
        "action_id": "api_act_review",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_id": "prod_01",
        "variant_id": "var_expensive",
        "amount_minor": 800000,
        "currency": "INR",
        "intent_purpose": "expensive purchase",
    }
    status_code, body = client.post("/api/v1/actions", json_body=payload, headers=headers)
    assert status_code == 200
    assert body["status"] == "AWAITING_APPROVAL"
    assert body["decision"] == "REVIEW"
    approval_id = body["approval_id"]
    assert approval_id is not None

    # 2. Grant approval via HTTP
    status_code, body = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json_body={"approver_id": "manager_alice"},
        headers=headers,
    )
    assert status_code == 200
    assert body["status"] == "APPROVED"
    assert body["approver_id"] == "manager_alice"

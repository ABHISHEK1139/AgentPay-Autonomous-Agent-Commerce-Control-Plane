"""Unit tests for the HTTP API layer, agent commerce routes, and emergency controls (§13, §21, §38, §67)."""

from __future__ import annotations

from typing import Any

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
    client = ApiTestClient(app).as_buyer()

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
    client = ApiTestClient(app).as_buyer()
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
    client = ApiTestClient(app).as_buyer()
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
    client = ApiTestClient(app).as_buyer()
    admin_client = client.as_admin()
    headers = {"x-tenant-id": "tenant_01"}

    # 1. Trigger kill switch pause via admin credentials (§21, §67)
    status_code, body = admin_client.post(
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

    # 3. Resume operations via admin credentials
    status_code, body = admin_client.post(
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
    client = ApiTestClient(app).as_buyer()
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


def test_emergency_endpoints_accessible_without_tenant_header() -> None:
    """Verify operator can query status, pause, and resume without X-Tenant-ID header (§21, §67)."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_admin()

    # 1. Query status without tenant header
    status, body = client.get("/api/v1/emergency/status", headers={"x-operator-id": "secops_01"})
    assert status == 200
    assert body["paused"] is False
    assert body["emergency_paused"] is False

    # 2. Pause without tenant header
    status, body = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "secops_01", "reason": "Sev 1 triage"},
        headers={"x-operator-id": "secops_01"},
    )
    assert status == 200
    assert body["status"] == "PAUSED"
    assert body["paused"] is True
    assert body["emergency_paused"] is True
    assert app.is_emergency_paused is True

    # 3. Resume without tenant header
    status, body = client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "secops_01"},
        headers={"x-operator-id": "secops_01"},
    )
    assert status == 200
    assert body["status"] == "ACTIVE"
    assert body["paused"] is False
    assert body["emergency_paused"] is False
    assert app.is_emergency_paused is False


def test_verify_audit_chain_script_cli_execution(tmp_path: Any) -> None:
    """Verify scripts/verify_audit_chain.py CLI returns 0 on valid export and 1 on tamper."""
    import json
    import subprocess

    from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
    from agentpay.domain.audit import AuditEventType

    ledger = InMemoryAuditLedger()
    e1 = ledger.record_event(
        event_type=AuditEventType.ACTION_REQUESTED,
        action_id="act_01",
        tenant_id="tenant_01",
        actor="user_01",
        data={"amount": 100},
        agent_id="agent_01",
    )
    e2 = ledger.record_event(
        event_type=AuditEventType.POLICY_EVALUATED,
        action_id="act_01",
        tenant_id="tenant_01",
        actor="policy_engine",
        data={"decision": "ALLOW"},
        agent_id="agent_01",
    )

    valid_file = tmp_path / "valid_audit.jsonl"
    lines = [
        json.dumps(
            {
                "event_id": e.event_id,
                "tenant_id": e.tenant_id,
                "actor": e.actor,
                "event_type": e.event_type.value,
                "timestamp": e.timestamp.isoformat(),
                "data": e.data,
                "previous_event_hash": e.previous_event_hash,
                "event_hash": e.event_hash,
                "agent_id": e.agent_id,
                "action_id": e.action_id,
            }
        )
        for e in [e1, e2]
    ]
    valid_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Valid run
    res_valid = subprocess.run(
        ["uv", "run", "python", "scripts/verify_audit_chain.py", str(valid_file)],
        capture_output=True,
        text=True,
    )
    assert res_valid.returncode == 0
    assert "SUCCESS" in res_valid.stdout

    # Tampered run
    tampered_file = tmp_path / "tampered_audit.jsonl"
    lines_tampered = list(lines)
    record = json.loads(lines_tampered[0])
    record["data"] = {"amount": 999999}  # Tamper payload
    lines_tampered[0] = json.dumps(record)
    tampered_file.write_text("\n".join(lines_tampered) + "\n", encoding="utf-8")

    res_tampered = subprocess.run(
        ["uv", "run", "python", "scripts/verify_audit_chain.py", str(tampered_file)],
        capture_output=True,
        text=True,
    )
    assert res_tampered.returncode == 1
    assert "FAILED" in res_tampered.stderr


def test_error_envelope_includes_trace_id_correlation() -> None:
    """Error envelopes must contain trace_id for forensic correlation (§13, §47)."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    # 1. Custom trace ID propagated into error response
    status, body = client.get(
        "/agent/catalog",
        headers={"x-trace-id": "trace_custom_12345"},
        auth=False,
    )
    assert status == 401
    assert "error" in body
    assert body["error"]["trace_id"] == "trace_custom_12345"
    assert "request_id" in body["error"]

    # 2. Auto-generated trace ID when none is provided
    status2, body2 = client.get("/agent/catalog", auth=False)
    assert status2 == 401
    assert "error" in body2
    assert body2["error"]["trace_id"] is not None
    assert body2["error"]["trace_id"].startswith("trace_")


def test_b_api_01_non_dict_json_body_returns_422() -> None:
    """B-API-01: Non-dict JSON bodies to mutating endpoints return 422 INVALID_REQUEST_BODY, never 500 (§13)."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    admin_client = ApiTestClient(app).as_admin()
    buyer_client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    # 1. List body to emergency pause
    status, body = admin_client.post(
        "/api/v1/emergency/pause", json_body=[1, 2, 3], headers=headers
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    # 2. String body to actions endpoint
    status, body = buyer_client.post(
        "/api/v1/actions", json_body="unexpected_string", headers=headers
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    # 3. List body to tool execution
    status, body = buyer_client.post(
        "/agent/tools/catalog_search/execute",
        json_body=[{"tool": "catalog_search"}],
        headers=headers,
    )
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    # 4. Integer body to token revocation
    status, body = admin_client.post("/api/v1/auth/tokens/revoke", json_body=12345, headers=headers)
    assert status == 422
    assert body["error"]["code"] == "INVALID_REQUEST_BODY"

    # 5. Non-dict to webhook returns 400 (consistent with webhook payload validation)
    status, body = buyer_client.post(
        "/api/v1/webhooks/payments", json_body=[1, 2, 3], headers=headers, auth=False
    )
    assert status == 400
    assert body["error"]["code"] == "INVALID_PAYLOAD"


def test_b_api_02_execute_approved_purchase_via_api() -> None:
    """B-API-02: Approved purchases can be executed via POST /api/v1/approvals/{id}/execute or approval_id in action submission (§20)."""
    orch, *_, variant_repo, inv_repo, _, _, _ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()
    headers = {"x-tenant-id": "tenant_01"}

    from agentpay.domain.commerce import Inventory, ProductVariant
    from agentpay.domain.money import Money

    variant_repo.save(
        "tenant_01",
        ProductVariant(
            variant_id="var_expensive_api",
            product_id="prod_01",
            sku="SKU-EXPENSIVE-API",
            name="Max Server API",
            price=Money(800000, "INR"),
        ),
    )
    inv_repo.save(
        "tenant_01",
        Inventory(
            variant_id="var_expensive_api",
            tenant_id="tenant_01",
            available=5,
            reserved=0,
        ),
    )

    # 1. Submit purchase requiring approval
    payload = {
        "action_id": "act_api_exec_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_id": "prod_01",
        "variant_id": "var_expensive_api",
        "amount_minor": 800000,
        "currency": "INR",
        "intent_purpose": "expensive server",
    }
    status, body = client.post("/api/v1/actions", json_body=payload, headers=headers)
    assert status == 200
    assert body["status"] == "AWAITING_APPROVAL"
    approval_id = body["approval_id"]
    assert approval_id is not None

    # 2. Grant approval
    status, body = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json_body={"approver_id": "manager_alice"},
        headers=headers,
    )
    assert status == 200
    assert body["status"] == "APPROVED"

    # 3. Execute approved purchase via POST /api/v1/approvals/{id}/execute
    exec_payload = {
        "action_id": "act_api_exec_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "variant_id": "var_expensive_api",
        "resource_id": "prod_01",
        "amount_minor": 800000,
        "currency": "INR",
        "quantity": 1,
    }
    status, body = client.post(
        f"/api/v1/approvals/{approval_id}/execute",
        json_body=exec_payload,
        headers=headers,
    )
    assert status == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["order_id"] is not None
    assert body["payment_id"] is not None

    # 4. Attempting to execute the already consumed approval returns 409
    status, body = client.post(
        f"/api/v1/approvals/{approval_id}/execute",
        json_body=exec_payload,
        headers=headers,
    )
    assert status == 409
    assert body["error"]["code"] == "APPROVAL_ALREADY_CONSUMED"

    # 5. Also verify execution via POST /api/v1/actions with approval_id parameter
    payload2 = {
        "action_id": "act_api_exec_02",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_id": "prod_01",
        "variant_id": "var_expensive_api",
        "amount_minor": 800000,
        "currency": "INR",
        "intent_purpose": "second expensive server",
    }
    status, body = client.post("/api/v1/actions", json_body=payload2, headers=headers)
    assert status == 200
    approval_id_2 = body["approval_id"]
    assert approval_id_2 is not None

    client.post(
        f"/api/v1/approvals/{approval_id_2}/approve",
        json_body={"approver_id": "manager_alice"},
        headers=headers,
    )

    payload2["approval_id"] = approval_id_2
    status, body = client.post("/api/v1/actions", json_body=payload2, headers=headers)
    assert status == 200
    assert body["status"] == "COMPLETED"
    assert body["decision"] == "ALLOW"
    assert body["order_id"] is not None

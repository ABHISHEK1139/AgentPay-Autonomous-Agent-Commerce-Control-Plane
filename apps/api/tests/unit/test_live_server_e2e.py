"""End-to-End Live Socket HTTP Server and Network Integration Tests (§10, §12, §13, §109).

Verifies the AgentPay platform over real TCP sockets using Python standard library
and the official Developer SDK (`agentpay.client` / `agentpay_sdk`):
1. Background HTTP server startup on an ephemeral localhost port.
2. Health check (`/api/v1/health`), OpenAPI spec (`/openapi.json`), and metrics (`/api/v1/metrics`).
3. CORS headers and preflight OPTIONS handling.
4. Product discovery and inventory lookup via SDK over network.
5. Autonomous purchase flow execution over real wire.
6. Approval-gated purchase flow, listing pending approvals, and granting via SDK.
7. Emergency kill switch pause and resume over network.
8. Signed HMAC-SHA256 payment webhook ingestion over HTTP.
9. Error envelope serialization, HTTP status mapping, and trace ID propagation.
10. Clean socket teardown and server shutdown.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest

from agentpay.api.factory import create_app
from agentpay.client import AgentPay, AgentPayClientError
from agentpay.server import create_server


@pytest.fixture(scope="module")
def live_server() -> Generator[tuple[str, int], None, None]:
    """Spins up a live multi-threaded HTTP server on an ephemeral OS-assigned port."""
    app = create_app(seed_demo_data=True, webhook_secret="whsec_live_test_secret_32bytes_len")
    # Port 0 instructs the OS kernel to bind an available ephemeral port
    server = create_server(host="127.0.0.1", port=0, app=app)
    addr = server.server_address
    host = str(addr[0])
    port = int(addr[1])

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)  # Allow socket to bind and start listening

    base_url = f"http://{host}:{port}"
    try:
        yield base_url, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_live_server_health_and_openapi(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server

    # 1. Health check
    req = urllib.request.Request(f"{base_url}/api/v1/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["status"] == "healthy"
        assert body["emergency_paused"] is False

    # 2. OpenAPI specification
    req_open = urllib.request.Request(f"{base_url}/openapi.json")
    with urllib.request.urlopen(req_open, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["openapi"] == "3.2.0"
        assert "/api/v1/actions" in body["paths"]

    # 3. Metrics exposition
    req_metrics = urllib.request.Request(f"{base_url}/api/v1/metrics")
    with urllib.request.urlopen(req_metrics, timeout=5) as resp:
        assert resp.status == 200
        metrics_body = json.loads(resp.read().decode("utf-8"))
        assert "metrics" in metrics_body
        assert "prometheus" in metrics_body


def test_live_server_cors_preflight(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server

    req = urllib.request.Request(
        f"{base_url}/api/v1/actions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Tenant-ID, Authorization",
        },
        method="OPTIONS",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_live_server_restricted_cors_origins() -> None:
    """Server enforces allowed_origins: permits configured origin and rejects unauthorized origins (§62, AP-010)."""
    app = create_app()
    server = create_server(
        host="127.0.0.1",
        port=0,
        app=app,
        allowed_origins=("https://control.internal",),
    )
    host, port = server.server_address[:2]
    host_str = host if isinstance(host, str) else host.decode("utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{host_str}:{port}"

    try:
        # 1. Allowed origin gets explicit Access-Control-Allow-Origin
        req_allowed = urllib.request.Request(
            f"{base_url}/api/v1/actions",
            headers={"Origin": "https://control.internal"},
            method="OPTIONS",
        )
        with urllib.request.urlopen(req_allowed, timeout=5) as resp:
            assert resp.status == 204
            assert resp.headers.get("Access-Control-Allow-Origin") == "https://control.internal"
            assert resp.headers.get("Vary") == "Origin"

        # 2. Disallowed origin gets 403 on preflight
        req_evil = urllib.request.Request(
            f"{base_url}/api/v1/actions",
            headers={"Origin": "https://attacker.evil"},
            method="OPTIONS",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_evil, timeout=5)
        assert exc_info.value.code == 403
        exc_info.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_live_server_sdk_catalog_discovery(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server
    sdk = AgentPay(
        base_url=base_url,
        api_key="apk_test_buyer_key_tenant_01",
        tenant_id="tenant_01",
    )

    # 1. List catalog
    cat = sdk.catalog.list_products()
    assert "products" in cat
    assert len(cat["products"]) >= 2
    product_ids = [p["product_id"] for p in cat["products"]]
    assert "prod_01" in product_ids

    # 2. Product inspection
    prod = sdk.catalog.get_product("prod_01")
    assert prod["product_id"] == "prod_01"
    assert prod["name"] == "Pro Laptop"

    # 3. Live inventory lookup
    inv = sdk.catalog.get_inventory("var_01")
    assert inv["variant_id"] == "var_01"
    assert inv["available"] > 0


def test_live_server_sdk_autonomous_purchase(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server
    sdk = AgentPay(
        base_url=base_url,
        api_key="apk_test_agent_key_tenant_01",
        tenant_id="tenant_01",
    )

    # Autonomous purchase under threshold (₹1,299 < ₹2,000 threshold)
    result = sdk.actions.request_purchase(
        product_id="prod_01",
        variant_id="var_01",
        amount_minor=129900,
        currency="INR",
        intent_purpose="Developer laptop acquisition",
    )
    assert result["status"] == "COMPLETED"
    assert "order_id" in result
    assert result["order_id"].startswith("ord_")
    assert "trace_id" in result


def test_live_server_sdk_approval_gated_flow(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server

    agent_sdk = AgentPay(
        base_url=base_url,
        api_key="apk_test_agent_key_tenant_01",
        tenant_id="tenant_01",
    )
    admin_sdk = AgentPay(
        base_url=base_url,
        bearer_token="test_bearer_admin_token",
        tenant_id="tenant_01",
    )

    # 1. Purchase exceeding ₹2,000 autonomous threshold (₹5,000)
    result = agent_sdk.actions.request_purchase(
        product_id="prod_02",
        variant_id="var_02",
        amount_minor=500000,
        currency="INR",
        intent_purpose="Server cluster upgrade",
    )
    assert result["status"] == "AWAITING_APPROVAL"
    approval_id = result.get("approval_id")
    assert approval_id is not None

    # 2. Admin lists pending approvals
    pending = admin_sdk.approvals.list_pending()
    assert any(a["approval_id"] == approval_id for a in pending)

    # 3. Admin grants approval
    grant_res = admin_sdk.approvals.grant(approval_id, approver_id="usr_admin_01")
    assert grant_res["status"] == "APPROVED"
    assert grant_res["approval_id"] == approval_id


def test_live_server_sdk_emergency_kill_switch(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server
    admin_sdk = AgentPay(
        base_url=base_url,
        bearer_token="test_bearer_admin_token",
        tenant_id="tenant_01",
    )

    # 1. Query initial status
    status = admin_sdk.emergency.get_status()
    assert status["paused"] is False

    # 2. Trigger pause
    pause_res = admin_sdk.emergency.pause(
        operator_id="usr_admin_01",
        reason="Routine live security audit simulation",
    )
    assert pause_res["status"] == "PAUSED"
    assert admin_sdk.emergency.get_status()["paused"] is True

    # 3. Actions should now fail closed with EMERGENCY_PAUSE_ACTIVE (HTTP 503)
    agent_sdk = AgentPay(
        base_url=base_url,
        api_key="apk_test_agent_key_tenant_01",
        tenant_id="tenant_01",
    )
    with pytest.raises(AgentPayClientError) as exc_info:
        agent_sdk.actions.request_purchase(
            product_id="prod_01",
            variant_id="var_01",
            amount_minor=129900,
            currency="INR",
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "EMERGENCY_PAUSE_ACTIVE"

    # 4. Resume operations
    resume_res = admin_sdk.emergency.resume(operator_id="usr_admin_01")
    assert resume_res["status"] == "ACTIVE"
    assert admin_sdk.emergency.get_status()["paused"] is False


def test_live_server_signed_payment_webhook(live_server: tuple[str, int]) -> None:
    base_url, _ = live_server
    secret = "whsec_live_test_secret_32bytes_len"
    tenant_id = "tenant_01"

    agent_sdk = AgentPay(
        base_url=base_url,
        api_key="apk_test_agent_key_tenant_01",
        tenant_id=tenant_id,
    )
    purchase = agent_sdk.actions.request_purchase(
        product_id="prod_01",
        variant_id="var_01",
        amount_minor=129900,
        currency="INR",
        intent_purpose="Webhook test purchase",
    )
    payment_id = purchase.get("payment_id")
    assert payment_id is not None

    payload: dict[str, Any] = {
        "event_id": "evt_live_webhook_01",
        "event_type": "payment.captured",
        "tenant_id": tenant_id,
        "payment_id": payment_id,
        "provider_payment_id": "ch_mock_live_999",
        "amount_minor": 129900,
        "currency": "INR",
        "status": "CAPTURED",
    }
    ts = int(datetime.now(UTC).timestamp())
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    canonical = f"{ts}.{raw_json}"
    signature = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/v1/webhooks/payments",
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Tenant-ID": tenant_id,
            "X-Webhook-Timestamp": str(ts),
            "X-Webhook-Signature": signature,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        res_body = json.loads(resp.read().decode("utf-8"))
        assert res_body["status"] == "PROCESSED"
        assert res_body["event_type"] == "payment.captured"


def test_live_server_unauthenticated_rejection_and_trace_id(
    live_server: tuple[str, int],
) -> None:
    base_url, _ = live_server
    invalid_sdk = AgentPay(
        base_url=base_url,
        api_key="apk_bogus_key_that_does_not_exist",
        tenant_id="tenant_01",
    )

    with pytest.raises(AgentPayClientError) as exc_info:
        invalid_sdk.catalog.list_products()

    err = exc_info.value
    assert err.status_code == 401
    assert err.code == "UNAUTHENTICATED"
    assert err.request_id is not None
    assert err.trace_id is not None
    assert "Invalid authentication credentials" in err.message


def test_live_server_request_body_size_limit_rejection(
    live_server: tuple[str, int],
) -> None:
    """B-SRV-01: Server rejects oversized payloads with HTTP 413 PAYLOAD_TOO_LARGE (§62)."""
    base_url, _ = live_server

    # 1. Declared Content-Length > 1 MiB is rejected before reading stream
    req = urllib.request.Request(
        f"{base_url}/api/v1/actions",
        data=b"dummy",
        headers={
            "Content-Type": "application/json",
            "Content-Length": "1048577",  # 1 byte over 1 MiB limit
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)

    assert exc_info.value.code == 413
    resp_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert resp_body["error"]["code"] == "PAYLOAD_TOO_LARGE"
    exc_info.value.close()

    # 2. Non-integer Content-Length is rejected with HTTP 400
    req_bad_cl = urllib.request.Request(
        f"{base_url}/api/v1/actions",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": "not_an_int",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info_bad:
        urllib.request.urlopen(req_bad_cl, timeout=5)

    assert exc_info_bad.value.code == 400
    resp_body_bad = json.loads(exc_info_bad.value.read().decode("utf-8"))
    assert resp_body_bad["error"]["code"] == "INVALID_CONTENT_LENGTH"
    exc_info_bad.value.close()

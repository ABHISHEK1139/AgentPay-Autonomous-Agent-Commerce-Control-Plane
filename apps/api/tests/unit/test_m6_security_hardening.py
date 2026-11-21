"""Milestone M6 Security Hardening: Comprehensive Automated Test Suite.

Covers all 10 security features mandated by M6:
- Feature 16: x402 Cryptographic Token Signing & Verification
- Feature 17: Strict Content-Type Validation (HTTP 415)
- Feature 18: Central HTTP Security Headers Injection
- Feature 19: CORS Default Origin Hardening
- Feature 20: Server Read Timeout & Slowloris Mitigation (HTTP 408)
- Feature 21: Webhook Error Sanitization & Observability Auth
- Feature 22: Protocol Amount Validation Harmonization (price > 0)
- Feature 23: SSRF DNS Rebinding TOCTOU Mitigation (IP pinning)
- Feature 24: Circuit Breaker Half-Open Concurrency Limiter
- Feature 25: AbuseDetector API Integration (Auth failure & Catalog scraping)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentpay.adapters.protocols.acp import ACPAdapter
from agentpay.adapters.protocols.base import MalformedPayloadError
from agentpay.adapters.protocols.uap import UAPAdapter
from agentpay.adapters.protocols.x402 import X402Adapter
from agentpay.api.app import ApiTestClient
from agentpay.api.factory import create_app
from agentpay.application.services.abuse_detector import AbuseDetector
from agentpay.application.use_cases.purchase_flow import PurchaseExecutionResult
from agentpay.config.config import AppConfig
from agentpay.domain.action_request import ActionStatus
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    PaymentAttempt,
    PaymentStatus,
)
from agentpay.domain.policy import PolicyDecision
from agentpay.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from agentpay.server import create_server

# ---------------------------------------------------------------------------
# Feature 16: x402 Cryptographic Token Signing & Verification
# ---------------------------------------------------------------------------


def test_f16_x402_cryptographic_token_signing_and_verification() -> None:
    secret = "f16_test_secret_for_x402_signing_32b"
    adapter = X402Adapter(signing_secret=secret)

    req = adapter.parse_payload(
        {
            "x402_version": "1.0",
            "challenge_id": "chal_f16_01",
            "resource_uri": "https://api.example.com/premium-content",
            "principal_id": "user_f16",
            "agent_id": "agent_f16",
            "tenant_id": "tenant_01",
            "amount_minor": 150000,
            "currency": "INR",
        }
    )

    result = PurchaseExecutionResult(
        action_id="x402_chal_f16_01",
        tenant_id="tenant_01",
        status=ActionStatus.COMPLETED,
        decision=PolicyDecision.ALLOW,
        payment=PaymentAttempt(
            attempt_id="pay_att_f16_01",
            intent_id="intent_f16_01",
            tenant_id="tenant_01",
            amount=Money(minor_units=150000, currency="INR"),
            status=PaymentStatus.CAPTURED,
            idempotency_key="idem_f16_01",
            provider="mock",
        ),
    )

    resp = adapter.from_execution_result(req, result)
    assert resp.authorization_token is not None
    assert resp.authorization_token.startswith("x402_")

    token = resp.authorization_token

    # 1. Valid token verification
    assert adapter.verify_settlement_token(token) is True
    assert adapter.verify_settlement_token(token, secret=secret) is True

    # 2. Tampered token payload
    parts = token.split(".")
    assert len(parts) == 2
    claims_b64, sig = parts[0], parts[1]
    tampered_claims = claims_b64[:-2] + "AA"
    assert adapter.verify_settlement_token(f"{tampered_claims}.{sig}") is False

    # 3. Tampered signature
    bad_sig = "a" * len(sig)
    assert adapter.verify_settlement_token(f"{claims_b64}.{bad_sig}") is False

    # 4. Wrong secret
    assert adapter.verify_settlement_token(token, secret="wrong_secret") is False

    # 5. Expired token
    expired_payload = {
        "challenge_id": "chal_f16_01",
        "resource_uri": "https://api.example.com/premium-content",
        "principal_id": "user_f16",
        "agent_id": "agent_f16",
        "tenant_id": "tenant_01",
        "amount_minor": 150000,
        "currency": "INR",
        "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
    }
    raw_json = json.dumps(expired_payload, separators=(",", ":")).encode("utf-8")
    exp_claims = "x402_" + base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    exp_sig = hmac.new(
        secret.encode("utf-8"), exp_claims.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert adapter.verify_settlement_token(f"{exp_claims}.{exp_sig}") is False


# ---------------------------------------------------------------------------
# Feature 17: Strict Content-Type Validation (HTTP 415)
# ---------------------------------------------------------------------------


def test_f17_strict_content_type_validation_on_mutating_requests() -> None:
    app = create_app(seed_demo_data=True)
    client = ApiTestClient(app).as_agent()

    # 1. Mutating request with valid Content-Type: application/json succeeds
    res_valid = client.request(
        "POST",
        "/agent/carts",
        headers={"Content-Type": "application/json", "X-Tenant-ID": "tenant_01"},
        json_body={"currency": "INR"},
    )
    assert res_valid.status_code == 201

    # 2. Mutating request with invalid Content-Type returns 415
    res_invalid_ct = client.request(
        "POST",
        "/agent/carts",
        headers={"Content-Type": "text/plain", "X-Tenant-ID": "tenant_01"},
        raw_body=b'{"currency": "INR"}',
    )
    assert res_invalid_ct.status_code == 415
    assert res_invalid_ct.body["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    # 3. Mutating request with missing Content-Type and non-empty body returns 415
    res_missing_ct = client.request(
        "POST",
        "/agent/carts",
        headers={"Content-Type": "__OMIT__", "X-Tenant-ID": "tenant_01"},
        raw_body=b'{"currency": "INR"}',
    )
    assert res_missing_ct.status_code == 415
    assert res_missing_ct.body["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    # 4. Non-mutating request (GET) does not enforce Content-Type
    res_get = client.request(
        "GET",
        "/agent/catalog",
        headers={"Content-Type": "text/plain", "X-Tenant-ID": "tenant_01"},
    )
    assert res_get.status_code == 200


# ---------------------------------------------------------------------------
# Feature 18: Central HTTP Security Headers Injection
# ---------------------------------------------------------------------------


def test_f18_central_security_headers_injection() -> None:
    app = create_app(seed_demo_data=True)
    client = ApiTestClient(app).as_agent()

    # Success response
    client.get("/api/v1/health")
    headers = client.last_response_headers

    assert headers.get("content-security-policy") == "default-src 'none'; frame-ancestors 'none'"
    assert (
        headers.get("strict-transport-security") == "max-age=63072000; includeSubDomains; preload"
    )
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    # Error response (404) also includes all 5 headers
    client.get("/non_existent_path")
    err_headers = client.last_response_headers
    assert (
        err_headers.get("content-security-policy") == "default-src 'none'; frame-ancestors 'none'"
    )
    assert (
        err_headers.get("strict-transport-security")
        == "max-age=63072000; includeSubDomains; preload"
    )
    assert err_headers.get("x-content-type-options") == "nosniff"
    assert err_headers.get("x-frame-options") == "DENY"
    assert err_headers.get("referrer-policy") == "strict-origin-when-cross-origin"


# ---------------------------------------------------------------------------
# Feature 19: CORS Default Origin Hardening
# ---------------------------------------------------------------------------


def test_f19_cors_default_origin_hardening() -> None:
    config = AppConfig()
    # Default allowed origins must be loopback only
    assert "http://localhost:3000" in config.cors_allowed_origins
    assert "http://127.0.0.1:3000" in config.cors_allowed_origins
    assert "*" not in config.cors_allowed_origins

    app = create_app()
    server = create_server(host="127.0.0.1", port=0, app=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)

    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        import urllib.error
        import urllib.request

        # 1. Allowed origin reflects specific origin, NOT wildcard *
        req_allowed = urllib.request.Request(
            f"{base_url}/api/v1/actions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(req_allowed, timeout=5) as resp:
            assert resp.status == 204
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"

        # 2. Unauthorized origin rejected with 403
        req_unauth = urllib.request.Request(
            f"{base_url}/api/v1/actions",
            headers={
                "Origin": "https://malicious-site.example",
                "Access-Control-Request-Method": "POST",
            },
            method="OPTIONS",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_unauth, timeout=5)
        assert exc_info.value.code == 403
        exc_info.value.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Feature 20: Server Read Timeout & Slowloris Mitigation
# ---------------------------------------------------------------------------


def test_f20_server_read_timeout_and_slowloris_mitigation() -> None:
    app = create_app()
    server = create_server(host="127.0.0.1", port=0, app=app)
    handler_cls: Any = server.RequestHandlerClass
    assert handler_cls.timeout == 10.0

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)

    try:
        # For a hermetic test without waiting 10 seconds, test the handler logic with a mock
        handler_instance = MagicMock()
        handler_instance.command = "POST"
        handler_instance.path = "/api/v1/actions"
        handler_instance.headers = {"Content-Length": "100", "Content-Type": "application/json"}
        handler_instance.rfile.read.side_effect = TimeoutError("Timed out waiting for bytes")
        handler_instance.wfile = MagicMock()

        # Call the actual _handle_request implementation on the handler class
        handler_cls._handle_request(handler_instance, "POST")
        handler_instance.send_response.assert_called_once_with(408)
        assert handler_instance.close_connection is True
        written = handler_instance.wfile.write.call_args[0][0]
        body = json.loads(written.decode("utf-8"))
        assert body["error"]["code"] == "REQUEST_TIMEOUT"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Feature 21: Webhook Error Sanitization & Observability Auth
# ---------------------------------------------------------------------------


def test_f21_webhook_error_sanitization_no_exception_leakage() -> None:
    app = create_app(seed_demo_data=True)
    client = ApiTestClient(app).as_agent()

    # Patch reconciler to raise an unexpected internal exception
    with patch.object(
        app.reconciler,
        "reconcile_webhook_event",
        side_effect=RuntimeError("Secret internal database password or stack trace detail"),
    ):
        res = client.post_webhook(
            event_type="payment.captured",
            payload={
                "event_id": "evt_test_sanitization_01",
                "payment_intent_id": "pi_123",
                "status": "SUCCEEDED",
            },
        )
        assert res.status_code == 500
        assert res.body["error"]["code"] == "RECONCILIATION_FAILED"
        # The internal exception string MUST NOT be leaked into the message
        assert "Secret internal database password" not in res.body["error"]["message"]
        assert (
            res.body["error"]["message"]
            == "An unexpected error occurred during webhook reconciliation"
        )


def test_f21_observability_auth_enforcement() -> None:
    app = create_app(require_observability_auth=True)
    client = ApiTestClient(app)

    # 1. Unauthenticated -> 401
    res_unauth = client.as_unauthenticated().get("/api/v1/metrics")
    assert res_unauth.status_code == 401
    assert res_unauth.body["error"]["code"] == "UNAUTHENTICATED"

    # 2. Buyer (non-operator) -> 403
    res_buyer = client.as_buyer().get("/api/v1/metrics")
    assert res_buyer.status_code == 403
    assert res_buyer.body["error"]["code"] == "UNAUTHORIZED_OBSERVABILITY_ACCESS"

    # 3. Admin -> 200
    res_admin = client.as_admin().get("/api/v1/metrics")
    assert res_admin.status_code == 200
    assert "metrics" in res_admin.body


# ---------------------------------------------------------------------------
# Feature 22: Protocol Amount Validation Harmonization
# ---------------------------------------------------------------------------


def test_f22_protocol_amount_validation_harmonization() -> None:
    acp = ACPAdapter()
    uap = UAPAdapter()
    x402 = X402Adapter()

    # ACP rejects <= 0
    with pytest.raises(MalformedPayloadError, match="Amount must be greater than zero"):
        acp.to_action_request(
            {
                "acp_version": "1.0",
                "request_id": "req_acp_01",
                "session_id": "sess_01",
                "buyer": {
                    "principal_id": "user_01",
                    "agent_id": "agent_01",
                    "tenant_id": "tenant_01",
                },
                "cart": {
                    "currency": "INR",
                    "items": [
                        {
                            "product_id": "prod_01",
                            "variant_id": "var_01",
                            "quantity": 1,
                            "unit_price_minor": 0,
                            "currency": "INR",
                        }
                    ],
                },
            }
        )

    # UAP rejects <= 0
    with pytest.raises(MalformedPayloadError, match="Amount must be greater than zero"):
        uap.to_action_request(
            {
                "uap_version": "1.0",
                "message_id": "uap_price_test",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_01", "quantity": 1, "price_minor": 0},
                },
            }
        )

    # x402 rejects <= 0
    with pytest.raises(MalformedPayloadError, match="Amount must be greater than zero"):
        x402.to_action_request(
            {
                "x402_version": "1.0",
                "challenge_id": "chal_01",
                "resource_uri": "https://api.example.com/item",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": 0,
                "currency": "INR",
            }
        )


# ---------------------------------------------------------------------------
# Feature 23: SSRF DNS Rebinding TOCTOU Mitigation
# ---------------------------------------------------------------------------


def test_f23_ssrf_dns_rebinding_mitigation() -> None:
    from agentpay.security.ssrf import open_pinned_connection, validate_outbound_url

    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        target = validate_outbound_url("https://api.openai.com/v1/chat/completions")
        assert target.pinned_ip == "93.184.216.34"
        assert target.path_with_query == "/v1/chat/completions"

        conn = open_pinned_connection(target, timeout=5.0)
        assert conn.host == "93.184.216.34"
        assert getattr(conn, "_pinned_server_hostname", None) == "api.openai.com"
        conn.close()


# ---------------------------------------------------------------------------
# Feature 24: Circuit Breaker Half-Open Concurrency Limiter
# ---------------------------------------------------------------------------


def test_f24_circuit_breaker_half_open_concurrency_limiter() -> None:
    cb = CircuitBreaker(
        name="f24_test_breaker",
        failure_threshold=1,
        recovery_timeout_seconds=0.01,
        half_open_success_threshold=2,
        half_open_max_concurrent_probes=1,
    )

    # Trip breaker
    with pytest.raises(RuntimeError), cb:
        raise RuntimeError("trip breaker")
    assert cb.state == CircuitState.OPEN

    # Wait for recovery window to allow transition to HALF_OPEN
    time.sleep(0.02)
    state_after_recovery: CircuitState = cb.state
    assert state_after_recovery == CircuitState.HALF_OPEN

    # First probe executes
    probe_started = threading.Event()
    probe_continue = threading.Event()
    rejected_error: list[Exception] = []

    def run_probe() -> None:
        try:
            with cb:
                probe_started.set()
                probe_continue.wait(timeout=2.0)
        except Exception as exc:
            rejected_error.append(exc)

    t1 = threading.Thread(target=run_probe)
    t1.start()
    probe_started.wait(timeout=2.0)

    # Second concurrent probe MUST be rejected because half_open_max_concurrent_probes = 1
    with pytest.raises(CircuitBreakerOpenError), cb:
        pass

    probe_continue.set()
    t1.join(timeout=2.0)
    assert len(rejected_error) == 0


# ---------------------------------------------------------------------------
# Feature 25: AbuseDetector API Integration
# ---------------------------------------------------------------------------


def test_f25_abuse_detector_auth_failure_and_catalog_query() -> None:
    abuse_detector = AbuseDetector(
        credential_stuffing_threshold=3,
        scraping_burst_threshold=3,
    )
    app = create_app(seed_demo_data=True, abuse_detector=abuse_detector)
    client = ApiTestClient(app)

    # 1. Test auth failures recorded
    for _ in range(3):
        res = client.get(
            "/api/v1/actions",
            headers={"Authorization": "Bearer invalid_token", "X-Tenant-ID": "tenant_01"},
            auth=False,
        )
        assert res.status_code == 401

    # Credential stuffing threshold reached: check tracking in abuse detector
    assert len(abuse_detector._auth_failures["127.0.0.1"]) == 3

    # 2. Test catalog scraping burst detection
    client_buyer = client.as_buyer()
    tenant_hdr = {"X-Tenant-ID": "tenant_01"}
    # 2 queries succeed
    r1 = client_buyer.get("/agent/catalog", headers=tenant_hdr)
    assert r1.status_code == 200
    r2 = client_buyer.get("/agent/catalog", headers=tenant_hdr)
    assert r2.status_code == 200

    # 3rd query reaches threshold (threshold=3) -> triggers RATE_LIMIT
    r3 = client_buyer.get("/agent/catalog", headers=tenant_hdr)
    assert r3.status_code == 429
    assert r3.body["error"]["code"] == "RATE_LIMIT_EXCEEDED"

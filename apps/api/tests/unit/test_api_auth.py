"""Unit and integration tests for API authentication and principal-to-tenant binding (§14, §15, §16, §49, §50, §67).

Validates:
- 401 UNAUTHENTICATED on missing, invalid, revoked, or expired credentials.
- 403 CROSS_TENANT_ACCESS_DENIED when asserted tenant mismatches credential tenant.
- 403 UNAUTHORIZED_OPERATOR when non-operators invoke emergency control routes.
- 200 OK when valid operator or admin invokes emergency routes.
- 200 OK for unauthenticated public discovery endpoints (/api/v1/health, /openapi.json).
- Seamless backwards compatibility for legacy test client usage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.identity import (
    Credential,
    CredentialStatus,
    CredentialType,
    UserRole,
)


@pytest.fixture
def api_client() -> ApiTestClient:
    """Fixture providing an ApiTestClient with standard test orchestrator."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    return ApiTestClient(app)


# ===========================================================================
# Group 1: Missing Authentication Credentials (401 UNAUTHENTICATED)
# ===========================================================================


def test_missing_credentials_on_catalog_route_returns_401(api_client: ApiTestClient) -> None:
    """GET /agent/catalog without credentials returns HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.get(
        "/agent/catalog", headers={"x-tenant-id": "tenant_01"}, auth=False
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert "request_id" in body["error"]
    assert "credentials" in body["error"]["message"].lower()


def test_missing_credentials_on_actions_route_returns_401(api_client: ApiTestClient) -> None:
    """POST /api/v1/actions without credentials returns HTTP 401 UNAUTHENTICATED."""
    payload = {
        "action_id": "act_test",
        "principal_id": "usr_01",
        "agent_id": "ag_01",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 1000,
    }
    status, body = api_client.post(
        "/api/v1/actions",
        json_body=payload,
        headers={"x-tenant-id": "tenant_01"},
        auth=False,
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_missing_credentials_on_emergency_pause_returns_401(api_client: ApiTestClient) -> None:
    """POST /api/v1/emergency/pause without credentials returns HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "op_01", "reason": "test"},
        auth=False,
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_missing_credentials_on_emergency_status_returns_401(api_client: ApiTestClient) -> None:
    """GET /api/v1/emergency/status without credentials returns HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.get("/api/v1/emergency/status", auth=False)
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_missing_credentials_on_protocol_routes_returns_401(api_client: ApiTestClient) -> None:
    """Protocol endpoints without credentials fail closed with HTTP 401 UNAUTHENTICATED."""
    endpoints = [
        "/api/v1/protocols/acp/checkout",
        "/api/v1/protocols/ap2/mandates",
        "/api/v1/protocols/uap/rpc",
        "/api/v1/protocols/x402/pay",
    ]
    for ep in endpoints:
        status, body = api_client.post(
            ep, json_body={}, headers={"x-tenant-id": "tenant_01"}, auth=False
        )
        assert status == 401
        assert body["error"]["code"] == "UNAUTHENTICATED"


def test_empty_bearer_token_returns_401(api_client: ApiTestClient) -> None:
    """Providing 'Bearer ' with empty secret returns HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.get(
        "/agent/catalog", headers={"Authorization": "Bearer   ", "x-tenant-id": "tenant_01"}
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_malformed_auth_scheme_returns_401(api_client: ApiTestClient) -> None:
    """Unsupported authorization schemes (e.g. Basic) return HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": "Basic dXNlcjpwYXNz", "x-tenant-id": "tenant_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


# ===========================================================================
# Group 2: Invalid, Revoked, and Expired Credentials (401 UNAUTHENTICATED)
# ===========================================================================


def test_unknown_bearer_token_returns_401(api_client: ApiTestClient) -> None:
    """A syntactically valid Bearer token not in repository returns HTTP 401."""
    status, body = api_client.get(
        "/agent/catalog",
        headers={
            "Authorization": "Bearer nonexistent_token_random_12345",
            "x-tenant-id": "tenant_01",
        },
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert "Invalid" in body["error"]["message"]


def test_unknown_api_key_returns_401(api_client: ApiTestClient) -> None:
    """An unrecorded API key returns HTTP 401 UNAUTHENTICATED."""
    status, body = api_client.get(
        "/agent/catalog",
        headers={"X-API-Key": "apk_unknown_hash_mismatch", "x-tenant-id": "tenant_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_revoked_credential_returns_401(api_client: ApiTestClient) -> None:
    """A revoked credential returns HTTP 401 UNAUTHENTICATED."""
    revoked_token = api_client.create_token(
        tenant_id="tenant_01",
        status=CredentialStatus.REVOKED,
    )
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {revoked_token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert "revoked" in body["error"]["message"].lower()


def test_runtime_revocation_immediately_enforces_401(api_client: ApiTestClient) -> None:
    """Revoking an active credential dynamically in the repository rejects subsequent calls."""
    token = "dynamic_revoke_token_999"
    api_client.create_token(
        tenant_id="tenant_01",
        raw_token=token,
    )
    # 1. Initial request succeeds
    status, _ = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 200

    # 2. Revoke credential
    secret_hash = Credential.hash_secret(token)
    cred = api_client.app.credential_repo.get_by_secret_hash(secret_hash)
    assert cred is not None
    api_client.app.credential_repo.revoke("tenant_01", cred.credential_id)

    # 3. Next request fails immediately
    status2, body2 = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status2 == 401
    assert body2["error"]["code"] == "UNAUTHENTICATED"


def test_expired_credential_by_status_returns_401(api_client: ApiTestClient) -> None:
    """A credential with status=EXPIRED returns HTTP 401 UNAUTHENTICATED."""
    token = api_client.create_token(
        tenant_id="tenant_01",
        status=CredentialStatus.EXPIRED,
    )
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert "expired" in body["error"]["message"].lower()


def test_expired_credential_by_timestamp_returns_401(api_client: ApiTestClient) -> None:
    """A credential with past expires_at timestamp returns HTTP 401 UNAUTHENTICATED."""
    token = api_client.create_token(
        tenant_id="tenant_01",
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert "expired" in body["error"]["message"].lower()


# ===========================================================================
# Group 3: Valid Authentication via Bearer Token & API Key (200 OK)
# ===========================================================================


def test_valid_bearer_token_authenticates_successfully(api_client: ApiTestClient) -> None:
    """Valid Bearer token grants access to protected routes with HTTP 200."""
    token = api_client.create_token(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 200
    assert "products" in body


def test_valid_api_key_authenticates_successfully(api_client: ApiTestClient) -> None:
    """Valid X-API-Key header grants access to protected routes with HTTP 200."""
    key = api_client.create_api_key(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"X-API-Key": key, "x-tenant-id": "tenant_01"},
    )
    assert status == 200
    assert "products" in body


def test_case_insensitive_bearer_prefix_authenticates(api_client: ApiTestClient) -> None:
    """Authorization header with lowercase 'bearer <token>' is successfully accepted."""
    token = api_client.create_token(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 200
    assert "products" in body


# ===========================================================================
# Group 4: Principal-to-Tenant Binding (403 CROSS_TENANT_ACCESS_DENIED)
# ===========================================================================


def test_tenant_mismatch_header_vs_credential_returns_403(api_client: ApiTestClient) -> None:
    """Presenting tenant_01 token while asserting X-Tenant-ID: tenant_02 triggers HTTP 403."""
    token = api_client.create_token(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_02"},
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"
    assert "tenant_01" in body["error"]["message"]
    assert "tenant_02" in body["error"]["message"]


def test_tenant_mismatch_on_action_execution_returns_403(api_client: ApiTestClient) -> None:
    """Action execution across tenant boundary is blocked with HTTP 403."""
    token = api_client.create_token(tenant_id="tenant_01")
    payload = {
        "action_id": "act_cross",
        "principal_id": "usr_01",
        "agent_id": "ag_01",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 1000,
    }
    status, body = api_client.post(
        "/api/v1/actions",
        json_body=payload,
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_attacker"},
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"


def test_tenant_mismatch_on_acp_checkout_returns_403(api_client: ApiTestClient) -> None:
    """ACP checkout fails closed when credential tenant differs from asserted tenant."""
    token = api_client.create_token(tenant_id="tenant_01")
    payload = {
        "acp_version": "1.0",
        "request_id": "r1",
        "buyer": {"principal_id": "u", "agent_id": "a", "tenant_id": "tenant_01"},
        "cart": {
            "items": [
                {
                    "product_id": "p",
                    "variant_id": "v",
                    "unit_price_minor": 100,
                    "currency": "INR",
                    "quantity": 1,
                }
            ]
        },
    }
    status, body = api_client.post(
        "/api/v1/protocols/acp/checkout",
        json_body=payload,
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_spoof"},
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"


def test_tenant_mismatch_on_ap2_mandates_returns_403(api_client: ApiTestClient) -> None:
    """AP2 mandate settlement fails closed on cross-tenant assertion."""
    token = api_client.create_token(tenant_id="tenant_01")
    payload = {
        "ap2_version": "1.0",
        "payment_request_id": "m1",
        "mandate_id": "man_1",
        "payer": {"principal_id": "u", "agent_id": "a", "tenant_id": "tenant_01"},
        "payee": {"merchant_id": "m", "order_ref": "o"},
        "amount": {"minor_units": 1000, "currency": "INR"},
    }
    status, body = api_client.post(
        "/api/v1/protocols/ap2/mandates",
        json_body=payload,
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_spoof"},
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"


def test_matching_tenant_and_credential_succeeds(api_client: ApiTestClient) -> None:
    """When X-Tenant-ID strictly matches credential tenant, request proceeds normally."""
    token = api_client.create_token(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}", "x-tenant-id": "tenant_01"},
    )
    assert status == 200
    assert "products" in body


# ===========================================================================
# Group 5: Emergency Kill-Switch Operator Authorization (403 vs 200)
# ===========================================================================


def test_non_operator_buyer_accessing_emergency_pause_returns_403(
    api_client: ApiTestClient,
) -> None:
    """Caller with BUYER role cannot trigger emergency pause."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.BUYER)
    status, body = api_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "buyer_01", "reason": "unauthorized attempt"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert "operator" in body["error"]["message"].lower()


def test_non_operator_auditor_accessing_emergency_resume_returns_403(
    api_client: ApiTestClient,
) -> None:
    """Caller with AUDITOR role cannot resume emergency kill switch."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.AUDITOR)
    status, body = api_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "auditor_01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_non_operator_finance_accessing_emergency_status_returns_403(
    api_client: ApiTestClient,
) -> None:
    """Caller with FINANCE role cannot access emergency kill switch status."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.FINANCE)
    status, body = api_client.get(
        "/api/v1/emergency/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_credential_without_role_accessing_emergency_returns_403(
    api_client: ApiTestClient,
) -> None:
    """Agent credential without assigned role cannot invoke emergency routes."""
    token = "agent_no_role_token_77"
    cred = Credential(
        credential_id="cred_norole",
        tenant_id="tenant_01",
        principal_id="ag_01",
        credential_type=CredentialType.BEARER_TOKEN,
        secret_hash=Credential.hash_secret(token),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        role=None,
    )
    api_client.app.credential_repo.save("tenant_01", cred)

    status, body = api_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "ag_01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_valid_agent_operator_accessing_emergency_pause_returns_200(
    api_client: ApiTestClient,
) -> None:
    """Caller with AGENT_OPERATOR role successfully pauses cluster operations."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.AGENT_OPERATOR)
    status, body = api_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "sec_op_01", "reason": "Incident triage"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert body["status"] == "PAUSED"
    assert body["paused"] is True
    assert api_client.app.is_emergency_paused is True


def test_valid_admin_role_accessing_emergency_resume_returns_200(
    api_client: ApiTestClient,
) -> None:
    """Caller with ADMIN role successfully resumes cluster operations."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.ADMIN)
    api_client.app.pause_emergency(operator_id="admin_01", reason="pre-pause")

    status, body = api_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "admin_01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert body["status"] == "ACTIVE"
    assert body["paused"] is False
    assert api_client.app.is_emergency_paused is False


def test_valid_operator_querying_emergency_status_returns_200(
    api_client: ApiTestClient,
) -> None:
    """Caller with AGENT_OPERATOR role queries emergency kill switch status."""
    token = api_client.create_token(tenant_id="tenant_01", role=UserRole.AGENT_OPERATOR)
    status, body = api_client.get(
        "/api/v1/emergency/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200
    assert "paused" in body
    assert "metadata" in body


# ===========================================================================
# Group 6: Public Endpoints & Edge Cases
# ===========================================================================


def test_health_and_openapi_remain_unauthenticated(api_client: ApiTestClient) -> None:
    """GET /api/v1/health and GET /openapi.json succeed without any credentials."""
    s1, b1 = api_client.get("/api/v1/health", auth=False)
    assert s1 == 200
    assert b1["status"] == "healthy"

    s2, b2 = api_client.get("/openapi.json", auth=False)
    assert s2 == 200
    assert b2["openapi"] == "3.1.0"


def test_missing_tenant_header_on_authenticated_request_returns_400(
    api_client: ApiTestClient,
) -> None:
    """An authenticated caller omitting X-Tenant-ID on non-protocol routes receives HTTP 400."""
    token = api_client.create_token(tenant_id="tenant_01")
    status, body = api_client.get(
        "/agent/catalog",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 400
    assert body["error"]["code"] == "MISSING_TENANT_HEADER"
    assert "X-Tenant-ID" in body["error"]["message"]


def test_both_bearer_and_api_key_provided_prefers_bearer(api_client: ApiTestClient) -> None:
    """When both Authorization and X-API-Key are present, Bearer takes precedence."""
    valid_token = api_client.create_token(tenant_id="tenant_01")
    status, _ = api_client.get(
        "/agent/catalog",
        headers={
            "Authorization": f"Bearer {valid_token}",
            "X-API-Key": "apk_completely_invalid_key",
            "x-tenant-id": "tenant_01",
        },
    )
    assert status == 200


# ===========================================================================
# Group 7: Backwards-Compatibility Verifications
# ===========================================================================


def test_existing_test_pattern_without_explicit_auth_passes_cleanly(
    api_client: ApiTestClient,
) -> None:
    """Proves legacy test calling syntax without credentials continues to function seamlessly."""
    status, body = api_client.get(
        "/agent/catalog",
        headers={"x-tenant-id": "tenant_01"},
    )
    assert status == 200
    assert "products" in body


def test_existing_emergency_pattern_without_explicit_auth_passes_cleanly(
    api_client: ApiTestClient,
) -> None:
    """Proves legacy emergency pause calls continue to succeed via default operator role."""
    status, body = api_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "sec_operator", "reason": "legacy test"},
    )
    assert status == 200
    assert body["status"] == "PAUSED"

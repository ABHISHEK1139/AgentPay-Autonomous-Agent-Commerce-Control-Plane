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

from agentpay.api.app import AgentPayApiApp, ApiTestClient, UnconfiguredClientAuthError
from agentpay.domain.commerce import (
    Inventory,
    Merchant,
    MerchantStatus,
    Product,
    ProductVariant,
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Credential,
    CredentialStatus,
    CredentialType,
    Tenant,
    TenantStatus,
    UserRole,
)
from agentpay.domain.money import Money
from agentpay.domain.policy import SpendingLimits


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
    """Unconfigured client without persona or explicit credentials is rejected (§14)."""
    with pytest.raises(UnconfiguredClientAuthError):
        api_client.get(
            "/agent/catalog",
            headers={"x-tenant-id": "tenant_01"},
        )


def test_unprivileged_default_client_accessing_emergency_pause_returns_403(
    api_client: ApiTestClient,
) -> None:
    """An unprivileged default client (BUYER role) cannot trigger emergency pause (§14, §67)."""
    status, body = api_client.as_buyer().post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "buyer_01", "reason": "unauthorized attempt"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert "operator" in body["error"]["message"].lower()


def test_fluent_admin_client_accessing_emergency_pause_succeeds(
    api_client: ApiTestClient,
) -> None:
    """A client elevated with as_admin() can trigger emergency pause (§14, §67)."""
    status, body = api_client.as_admin().post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "sec_admin", "reason": "authorized incident response"},
    )
    assert status == 200
    assert body["status"] == "PAUSED"


def test_fluent_unauthenticated_client_accessing_emergency_pause_returns_401(
    api_client: ApiTestClient,
) -> None:
    """A client with as_unauthenticated() is rejected with HTTP 401 UNAUTHENTICATED (§14)."""
    status, body = api_client.as_unauthenticated().post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "op_01", "reason": "test"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_fluent_agent_client_accessing_emergency_pause_returns_403(
    api_client: ApiTestClient,
) -> None:
    """An agent client with as_agent() is rejected with HTTP 403 UNAUTHORIZED_OPERATOR (§14, §67)."""
    status, body = api_client.as_agent("agent_01").post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "agent_01", "reason": "test"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_fluent_buyer_client_accessing_emergency_pause_returns_403(
    api_client: ApiTestClient,
) -> None:
    """A client explicitly scoped with as_buyer() is rejected with HTTP 403 UNAUTHORIZED_OPERATOR (§14, §67)."""
    status, body = api_client.as_buyer().post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "buyer_02", "reason": "test"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_fluent_helpers_immutability_and_chaining(
    api_client: ApiTestClient,
) -> None:
    """Fluent helper calls return scoped clones without mutating base client default role."""
    admin_client = api_client.as_admin()
    unauth_client = api_client.as_unauthenticated()
    agent_client = api_client.as_agent("agent_01")
    buyer_client = api_client.as_buyer()

    # Base client remains unconfigured
    assert api_client.default_role is None
    assert api_client._auth_mode == "unconfigured"

    # Admin client succeeds on emergency
    s_admin, _ = admin_client.get("/api/v1/emergency/status")
    assert s_admin == 200

    # Unauthenticated client rejected with 401
    s_unauth, _ = unauth_client.get("/api/v1/emergency/status")
    assert s_unauth == 401

    # Agent client rejected with 403
    s_agent, _ = agent_client.get("/api/v1/emergency/status")
    assert s_agent == 403

    # Explicit buyer client rejected with 403
    s_buyer, _ = buyer_client.get("/api/v1/emergency/status")
    assert s_buyer == 403

    # Base unconfigured client is rejected when calling protected endpoints
    with pytest.raises(UnconfiguredClientAuthError):
        api_client.get("/api/v1/emergency/status")


def test_token_cache_partitioned_by_tenant_principal_and_role(
    api_client: ApiTestClient,
) -> None:
    """Tokens are cached by (tenant_id, principal_id, role) preventing cross-role collisions."""
    token_buyer = api_client.get_or_create_token("tenant_01", role=UserRole.BUYER)
    token_admin = api_client.get_or_create_token("tenant_01", role=UserRole.ADMIN)
    token_agent = api_client.get_or_create_token("tenant_01", role=None, principal_id="agent_01")

    assert token_buyer != token_admin
    assert token_buyer != token_agent
    assert token_admin != token_agent

    # Re-fetching returns identical cached token
    assert api_client.get_or_create_token("tenant_01", role=UserRole.BUYER) == token_buyer
    assert api_client.get_or_create_token("tenant_01", role=UserRole.ADMIN) == token_admin
    assert (
        api_client.get_or_create_token("tenant_01", role=None, principal_id="agent_01")
        == token_agent
    )


def test_b_pause_01_scoped_emergency_pause_isolates_tenants(
    api_client: ApiTestClient,
) -> None:
    """B-PAUSE-01: Scoped emergency pause halts targeted tenant without affecting others (§21, §67)."""
    orch = api_client.app.orchestrator
    now = datetime.now(UTC)

    # Seed tenant_02 in orchestrator repos so tenant_02 can perform actions
    if orch._tenant_repo is not None:
        orch._tenant_repo.save(
            Tenant(tenant_id="tenant_02", name="Tenant Two", status=TenantStatus.ACTIVE)
        )
    if orch._agent_repo is not None:
        orch._agent_repo.save(
            "tenant_02",
            Agent(
                agent_id="agent_02",
                tenant_id="tenant_02",
                principal_id="user_02",
                issuer="https://auth.agentpay.local",
                capabilities=("catalog.search", "payment.request", "checkout.prepare"),
                scopes=("commerce:read", "commerce:write"),
                created_at=now,
                expires_at=now + timedelta(days=30),
                status=AgentStatus.ACTIVE,
            ),
        )
    if orch._permissions_repo is not None:
        orch._permissions_repo.save(
            "tenant_02",
            AgentPermissions(
                agent_id="agent_02",
                tenant_id="tenant_02",
                capabilities=("catalog.search", "payment.request", "checkout.prepare"),
                limits=SpendingLimits(
                    per_transaction_minor=1000000,
                    daily_minor=5000000,
                    requires_approval_above_minor=500000,
                ),
                allowed_categories=("electronics",),
            ),
        )
    if orch._merchant_repo is not None:
        orch._merchant_repo.save(
            "tenant_02",
            Merchant(
                merchant_id="merch_02",
                tenant_id="tenant_02",
                name="Merchant Two",
                status=MerchantStatus.ACTIVE,
            ),
        )
    if orch._product_repo is not None:
        orch._product_repo.save(
            "tenant_02",
            Product(
                product_id="prod_02",
                tenant_id="tenant_02",
                merchant_id="merch_02",
                name="Gadget",
                category="electronics",
                description="Developer Gadget",
            ),
        )
    if orch._variant_repo is not None:
        orch._variant_repo.save(
            "tenant_02",
            ProductVariant(
                variant_id="var_02",
                product_id="prod_02",
                name="Gadget V2",
                sku="SKU-02",
                price=Money(1000, "INR"),
            ),
        )
    if orch._inventory_repo is not None:
        orch._inventory_repo.save(
            "tenant_02",
            Inventory(variant_id="var_02", tenant_id="tenant_02", available=50, reserved=0),
        )

    admin_client = api_client.as_admin()

    # 1. Engage scoped pause specifically for tenant_01
    s_pause, pause_body = admin_client.post(
        "/api/v1/emergency/pause",
        json_body={
            "operator_id": "sec_op",
            "reason": "Incident in tenant_01",
            "scope": "tenant_01",
        },
    )
    assert s_pause == 200
    assert pause_body["status"] == "PAUSED"
    assert "tenant_01" in pause_body["scopes"]
    assert api_client.app.is_emergency_paused_for("tenant_01") is True
    assert api_client.app.is_emergency_paused_for("tenant_02") is False

    # 2. tenant_01 request fails closed with 503 EMERGENCY_PAUSE_ACTIVE
    action_payload_01 = {
        "action_id": "act_t1_01",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "resource_type": "product_variant",
        "resource_id": "prod_01",
        "variant_id": "var_01",
        "amount_minor": 1000,
        "currency": "INR",
    }
    s1, b1 = api_client.as_agent("agent_01", tenant_id="tenant_01").post(
        "/api/v1/actions",
        json_body=action_payload_01,
        headers={"x-tenant-id": "tenant_01"},
    )
    assert s1 == 503
    assert b1["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"

    # 3. tenant_02 request succeeds (200) - unaffected by tenant_01 pause!
    action_payload_02 = {
        "action_id": "act_t2_01",
        "principal_id": "user_02",
        "agent_id": "agent_02",
        "resource_type": "product_variant",
        "resource_id": "prod_02",
        "variant_id": "var_02",
        "amount_minor": 1000,
        "currency": "INR",
    }
    s2, b2 = api_client.as_agent("agent_02", tenant_id="tenant_02").post(
        "/api/v1/actions",
        json_body=action_payload_02,
        headers={"x-tenant-id": "tenant_02"},
    )
    assert s2 == 200
    assert b2["status"] in ("COMPLETED", "EXECUTED")

    # 4. Resume tenant_01
    s_res, res_body = admin_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "sec_op", "scope": "tenant_01"},
    )
    assert s_res == 200
    assert res_body["status"] == "ACTIVE"
    assert api_client.app.is_emergency_paused_for("tenant_01") is False

    # 5. Global pause ("ALL") halts both tenants
    s_pause_all, _ = admin_client.post(
        "/api/v1/emergency/pause",
        json_body={
            "operator_id": "sec_op",
            "reason": "Platform-wide maintenance",
            "scope": "ALL",
        },
    )
    assert s_pause_all == 200
    assert api_client.app.is_emergency_paused_for("tenant_01") is True
    assert api_client.app.is_emergency_paused_for("tenant_02") is True

    s1_all, _ = api_client.as_agent("agent_01", tenant_id="tenant_01").post(
        "/api/v1/actions",
        json_body=action_payload_01,
        headers={"x-tenant-id": "tenant_01"},
    )
    assert s1_all == 503

    s2_all, _ = api_client.as_agent("agent_02", tenant_id="tenant_02").post(
        "/api/v1/actions",
        json_body=action_payload_02,
        headers={"x-tenant-id": "tenant_02"},
    )
    assert s2_all == 503

    # Clean up by resuming ALL
    admin_client.post(
        "/api/v1/emergency/resume", json_body={"operator_id": "sec_op", "scope": "ALL"}
    )
    assert api_client.app.is_emergency_paused is False


def test_b_obs_01_observability_and_sbom_access_control(api_client: ApiTestClient) -> None:
    """B-OBS-01: Gating and fail-closed authentication on metrics and SBOM routes (§14, §47, §57)."""
    # 1. Default mode (require_observability_auth=False): unauthenticated succeeds
    s_m, b_m = api_client.as_unauthenticated().get("/api/v1/metrics")
    assert s_m == 200
    assert "metrics" in b_m

    s_s, b_s = api_client.as_unauthenticated().get("/api/v1/security/sbom")
    assert s_s == 200
    assert b_s["format"] == "CycloneDX-1.5"

    # 2. Even when require_observability_auth=False, invalid/tampered token fails closed (401)
    s_tampered_m, b_tampered_m = api_client.get(
        "/api/v1/metrics",
        headers={"authorization": "Bearer bad_token_123"},
        auth=False,
    )
    assert s_m != 401
    assert s_tampered_m == 401
    assert b_tampered_m["error"]["code"] == "UNAUTHENTICATED"

    s_tampered_s, b_tampered_s = api_client.get(
        "/api/v1/security/sbom",
        headers={"authorization": "Bearer bad_token_123"},
        auth=False,
    )
    assert s_tampered_s == 401
    assert b_tampered_s["error"]["code"] == "UNAUTHENTICATED"

    # 3. Hardened mode (require_observability_auth=True): strict authentication enforced
    hardened_app = AgentPayApiApp(
        orchestrator=api_client.app.orchestrator,
        require_observability_auth=True,
    )
    hardened_client = ApiTestClient(hardened_app)

    # 3a. Unauthenticated access is rejected with HTTP 401
    s_hard_unauth_m, b_hard_unauth_m = hardened_client.as_unauthenticated().get("/api/v1/metrics")
    assert s_hard_unauth_m == 401
    assert b_hard_unauth_m["error"]["code"] == "UNAUTHENTICATED"

    s_hard_unauth_s, b_hard_unauth_s = hardened_client.as_unauthenticated().get(
        "/api/v1/security/sbom"
    )
    assert s_hard_unauth_s == 401
    assert b_hard_unauth_s["error"]["code"] == "UNAUTHENTICATED"

    # 3b. Non-operator role (BUYER) is rejected with HTTP 403 UNAUTHORIZED_OBSERVABILITY_ACCESS
    s_hard_buyer_m, b_hard_buyer_m = hardened_client.as_buyer().get("/api/v1/metrics")
    assert s_hard_buyer_m == 403
    assert b_hard_buyer_m["error"]["code"] == "UNAUTHORIZED_OBSERVABILITY_ACCESS"

    s_hard_buyer_s, b_hard_buyer_s = hardened_client.as_buyer().get("/api/v1/security/sbom")
    assert s_hard_buyer_s == 403
    assert b_hard_buyer_s["error"]["code"] == "UNAUTHORIZED_OBSERVABILITY_ACCESS"

    # 3c. Admin role succeeds (200 OK)
    s_hard_admin_m, b_hard_admin_m = hardened_client.as_admin().get("/api/v1/metrics")
    assert s_hard_admin_m == 200
    assert "metrics" in b_hard_admin_m

    s_hard_admin_s, b_hard_admin_s = hardened_client.as_admin().get("/api/v1/security/sbom")
    assert s_hard_admin_s == 200
    assert b_hard_admin_s["format"] == "CycloneDX-1.5"

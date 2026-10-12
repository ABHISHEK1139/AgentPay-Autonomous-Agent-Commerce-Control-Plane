"""Milestone 7 Empirical Stress Harness: Secure Auth Profile & PII/PCI Error Sanitization.

Validates invariants AP-009 and AP-015 (§14, §15, §20, §40, §45, §67):
1. Probe 1 (Production Tenant Derivation & Header Mismatch Rejection):
   - In production-like environments, authenticated tenant is derived from credentials if header is omitted.
   - Caller asserting mismatched X-Tenant-ID header is rejected with HTTP 403 CROSS_TENANT_ACCESS_DENIED.
2. Probe 2 (Agent Impersonation Prevention):
   - Authenticated agent cannot submit action asserting another agent_id (HTTP 403 AGENT_IMPERSONATION_DENIED).
3. Probe 3 (Agent Approval Authority Demarcation):
   - Authenticated agent cannot approve or reject human approvals (HTTP 403 AGENT_CANNOT_APPROVE).
4. Probe 4 (Production Observability Auth Default):
   - Sandbox and production environments default require_observability_auth=True, rejecting unauthenticated access.
5. Probe 5 (PII / PCI & Provider Secret Redaction):
   - Nested error objects, headers, and payloads redact provider signatures, webhook secrets, and card data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.config.config import AppConfig, Environment
from agentpay.domain.audit import SensitiveFieldRedactor
from agentpay.domain.identity import UserRole


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def run_probe_1_production_tenant_derivation_and_mismatch() -> bool:
    """Probe 1: In production mode, tenant is derived from credentials, and asserted mismatches are rejected."""
    prod_config = AppConfig(
        environment=Environment.PRODUCTION,
        database_url="sqlite:///:memory:",
        cors_allowed_origins=("https://app.agentpay.internal",),
        seed_demo_data=False,
    )
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch, config=prod_config)
    client = ApiTestClient(app).as_buyer()

    # 1. Calling tenant-scoped route WITHOUT X-Tenant-ID header derives tenant from authenticated credential
    status, body = client.get("/agent/catalog")
    assert status == 200, f"Expected 200 with derived tenant, got {status}: {body}"
    assert "products" in body

    # 2. Calling tenant-scoped route with MISMATCHED X-Tenant-ID header fails closed (403)
    token_t1 = client.get_or_create_token(tenant_id="tenant_01", role=UserRole.BUYER)
    status_mismatch, body_mismatch = client.get(
        "/agent/catalog",
        headers={"x-tenant-id": "tenant_evil_mismatch", "authorization": f"Bearer {token_t1}"},
    )
    assert status_mismatch == 403
    assert body_mismatch["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"
    return True


def run_probe_2_agent_impersonation_prevention() -> bool:
    """Probe 2: Authenticated agent cannot submit an action claiming to be another agent (§14, §15, AP-009)."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    agent_client = ApiTestClient(app).as_agent("agent_01", tenant_id="tenant_01")

    # Agent_01 tries to claim action as agent_imposter
    status, body = agent_client.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_01"},
        json_body={
            "action_id": "act_m7_impersonate",
            "principal_id": "user_01",
            "agent_id": "agent_imposter",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 1000,
        },
    )
    assert status == 403
    assert body["error"]["code"] == "AGENT_IMPERSONATION_DENIED"
    assert "agent_imposter" in body["error"]["message"]

    # Legitimate agent_01 succeeds
    status_ok, body_ok = agent_client.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_01"},
        json_body={
            "action_id": "act_m7_legit",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 129900,
        },
    )
    assert status_ok == 200
    assert body_ok["status"] in ("COMPLETED", "EXECUTED")
    return True


def run_probe_3_agent_cannot_approve_decisions() -> bool:
    """Probe 3: Authenticated agent cannot grant or reject human approvals (§2.1, §20, AP-009)."""
    orch, *_, variant_repo, inv_repo, _, _, _approval_repo = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client_buyer = ApiTestClient(app).as_buyer()
    client_agent = ApiTestClient(app).as_agent("agent_01", tenant_id="tenant_01")
    client_admin = ApiTestClient(app).as_admin()

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

    # 1. Trigger human review requirement with large transaction
    status_req, body_req = client_buyer.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_01"},
        json_body={
            "action_id": "act_m7_needs_review",
            "principal_id": "user_01",
            "agent_id": "agent_01",
            "resource_id": "prod_01",
            "variant_id": "var_expensive",
            "amount_minor": 800000,
        },
    )
    assert status_req == 200
    assert body_req["status"] == "AWAITING_APPROVAL"
    assert body_req["decision"] == "REVIEW"
    approval_id = body_req["approval_id"]

    # 2. Agent attempts to approve: BLOCKED with 403 AGENT_CANNOT_APPROVE
    status_agent_appr, body_agent_appr = client_agent.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"x-tenant-id": "tenant_01"},
        json_body={"approver_id": "agent_01"},
    )
    assert status_agent_appr == 403
    assert body_agent_appr["error"]["code"] == "AGENT_CANNOT_APPROVE"

    # 3. Agent attempts to reject: BLOCKED with 403 AGENT_CANNOT_APPROVE
    status_agent_rej, body_agent_rej = client_agent.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers={"x-tenant-id": "tenant_01"},
        json_body={"approver_id": "agent_01", "reason": "Agent unilateral cancellation"},
    )
    assert status_agent_rej == 403
    assert body_agent_rej["error"]["code"] == "AGENT_CANNOT_APPROVE"

    # 4. Human admin approves: SUCCEEDS with 200 OK
    status_admin, body_admin = client_admin.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers={"x-tenant-id": "tenant_01"},
        json_body={"approver_id": "human_supervisor_01"},
    )
    assert status_admin == 200
    assert body_admin["status"] == "APPROVED"
    return True


def run_probe_4_production_observability_auth_default() -> bool:
    """Probe 4: Sandbox and production configs default require_observability_auth=True (§47, AP-009)."""
    # 1. Sandbox configuration without explicit override defaults to requiring auth
    sandbox_cfg = AppConfig.from_env(
        {
            "AGENTPAY_ENVIRONMENT": "sandbox",
            "RAZORPAY_KEY_ID": "rzp_test_real_key_32bytes",
            "RAZORPAY_KEY_SECRET": "rzp_secret_real_secret_32bytes",
        }
    )
    assert sandbox_cfg.require_observability_auth is True

    # 2. Production configuration defaults to requiring auth
    prod_cfg = AppConfig.from_env(
        {
            "AGENTPAY_ENVIRONMENT": "production",
            "AGENTPAY_DATABASE_URL": "postgresql://user:pass@localhost:5432/agentpay",
            "AGENTPAY_CORS_ALLOWED_ORIGINS": "https://control.agentpay.internal",
        }
    )
    assert prod_cfg.require_observability_auth is True

    # 3. Local/development defaults to False
    local_cfg = AppConfig.from_env({"AGENTPAY_ENVIRONMENT": "local"})
    assert local_cfg.require_observability_auth is False
    return True


def run_probe_5_pii_pci_provider_secret_redaction() -> bool:
    """Probe 5: SensitiveFieldRedactor cleanly redacts provider signatures, secrets, and card data (§40, §45, AP-015)."""
    raw_payload = {
        "event_id": "evt_test_123",
        "nested_details": {
            "card_number": "4111222233334444",
            "pan": "4111222233334444",
            "cvv": "123",
            "cvv2": "999",
            "secret_key": "sec_very_secret_key",
            "webhook_secret": "whsec_super_secret",
            "x-razorpay-signature": "abcdef1234567890abcdef1234567890",
            "stripe-signature": "t=123456,v1=abcdef1234567890",
            "signature": "hmac_signature_bytes",
            "authorization": "Bearer super_secret_token",
            "regular_info": "safe_order_summary",
        },
        "items": [
            {"product_id": "prod_01", "price_minor": 1000},
            {"pin": "1234", "security_pin": "5678"},
        ],
    }

    sanitized = SensitiveFieldRedactor.redact(raw_payload)

    # Safe data preserved
    assert sanitized["event_id"] == "evt_test_123"
    assert sanitized["nested_details"]["regular_info"] == "safe_order_summary"
    assert sanitized["items"][0]["product_id"] == "prod_01"

    # Sensitive data redacted
    assert sanitized["nested_details"]["card_number"] == "[REDACTED]"
    assert sanitized["nested_details"]["pan"] == "[REDACTED]"
    assert sanitized["nested_details"]["cvv"] == "[REDACTED]"
    assert sanitized["nested_details"]["cvv2"] == "[REDACTED]"
    assert sanitized["nested_details"]["secret_key"] == "[REDACTED]"
    assert sanitized["nested_details"]["webhook_secret"] == "[REDACTED]"
    assert sanitized["nested_details"]["x-razorpay-signature"] == "[REDACTED]"
    assert sanitized["nested_details"]["stripe-signature"] == "[REDACTED]"
    assert sanitized["nested_details"]["signature"] == "[REDACTED]"
    assert sanitized["nested_details"]["authorization"] == "[REDACTED]"
    assert sanitized["items"][1]["pin"] == "[REDACTED]"
    assert sanitized["items"][1]["security_pin"] == "[REDACTED]"
    return True


def main() -> None:
    log("=" * 72)
    log("AGENTPAY MILESTONE 7: SECURE AUTH PROFILE & REDACTION ADVERSARIAL HARNESS")
    log("=" * 72)

    probes = [
        (
            "probe_1_production_tenant_derivation_and_mismatch",
            run_probe_1_production_tenant_derivation_and_mismatch,
        ),
        (
            "probe_2_agent_impersonation_prevention",
            run_probe_2_agent_impersonation_prevention,
        ),
        (
            "probe_3_agent_cannot_approve_decisions",
            run_probe_3_agent_cannot_approve_decisions,
        ),
        (
            "probe_4_production_observability_auth_default",
            run_probe_4_production_observability_auth_default,
        ),
        (
            "probe_5_pii_pci_provider_secret_redaction",
            run_probe_5_pii_pci_provider_secret_redaction,
        ),
    ]

    for name, fn in probes:
        try:
            res = fn()
            assert res is True
            log(f"[*] {name}: PASS")
        except Exception as exc:
            log(f"[!] {name}: FAIL -> {exc}")
            raise

    log("=" * 72)
    log("[+] VERDICT: ALL MILESTONE 7 ADVERSARIAL PROBES SATISFIED -> PASS")


if __name__ == "__main__":
    main()

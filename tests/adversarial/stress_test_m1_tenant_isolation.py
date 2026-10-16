"""Milestone 1 Empirical Tenant Isolation & Mapping Integrity Stress Harness (§49, §50, §51, AP-001, AP-002).

Exhaustively probes:
1. Probe 1: Elimination of `tenant_01` fallback in adapters, API routes, and background reconciliation.
2. Probe 2: Unmapped provider payment ID quarantine (HTTP 422, zero mutations, SYSTEM security alert).
3. Probe 3: Cross-tenant spoofing and hostile tenant injection rejection.
4. Probe 4: Stored mapping correctness under legitimate purchase orchestration and multi-tenant isolation.
"""

from __future__ import annotations

import concurrent.futures
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

from agentpay.adapters.razorpay_payment_provider import RazorpayPaymentProvider
from agentpay.adapters.stripe_payment_provider import StripePaymentProvider
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import Inventory
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderTransactionMapping,
)


def log(msg: str) -> None:
    """Output message to stdout without lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _seed_environment(
    tenant_id: str,
    attempt_id: str = "att_test_01",
    order_id: str = "ord_test_01",
    variant_id: str = "var_01",
    amount_minor: int = 149900,
    currency: str = "INR",
    payment_provider: Any = None,
) -> tuple[AgentPayApiApp, ApiTestClient, dict[str, Any]]:
    """Seed a hermetic test environment with an active order, attempt, and inventory."""
    orch, _, _, _, _, _, _, inventory_repo, _, audit_ledger, _ = setup_orchestrator(
        tenant_id=tenant_id
    )
    if payment_provider is not None:
        orch._payment_provider = payment_provider
    app = AgentPayApiApp(orchestrator=orch, webhook_secret="whsec_unit_test_only_secret_32bytes")
    client = ApiTestClient(app)

    # Seed baseline inventory
    inv = Inventory(
        variant_id=variant_id,
        tenant_id=tenant_id,
        available=10,
        reserved=2,
    )
    inventory_repo.save(tenant_id, inv)

    # Seed PaymentAttempt in CREATED status
    attempt = PaymentAttempt(
        attempt_id=attempt_id,
        intent_id=f"intent_{attempt_id}",
        tenant_id=tenant_id,
        amount=Money(amount_minor, currency),
        status=PaymentStatus.CREATED,
        idempotency_key=f"idem_{attempt_id}",
        provider="mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save(tenant_id, attempt)

    # Seed Order in PENDING_PAYMENT status
    order = Order(
        order_id=order_id,
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id=variant_id,
        quantity=2,
        total_amount=Money(amount_minor, currency),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id=attempt_id,
    )
    assert orch._order_repo is not None
    orch._order_repo.save(tenant_id, order)

    return (
        app,
        client,
        {
            "orchestrator": orch,
            "attempt": attempt,
            "order": order,
            "inventory": inv,
            "payment_repo": orch._payment_repo,
            "order_repo": orch._order_repo,
            "inventory_repo": inventory_repo,
            "audit_ledger": audit_ledger,
            "provider_mapping_repo": orch._provider_mapping_repo,
        },
    )


# ===========================================================================
# Probe 1: Elimination of tenant_01 Fallback
# ===========================================================================
def probe_elimination_of_tenant_01_fallback() -> dict[str, Any]:
    """Verify that unannotated webhooks NEVER default to tenant_01."""
    log("[->] Executing Probe 1 (Elimination of 'tenant_01' Fallback)...")
    violations: list[str] = []

    # 1.1 Direct Adapter Parser Checks
    rzp_adapter = RazorpayPaymentProvider(key_id="rzp_test_k", key_secret="rzp_test_s")
    raw_rzp_no_tenant = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fallback_probe_01",
                    "amount": 100000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"something_else": "value"},  # NO tenant_id
                }
            }
        },
    }
    raw_rzp_bytes = json.dumps(raw_rzp_no_tenant, separators=(",", ":")).encode("utf-8")
    sig_rzp = rzp_adapter.generate_webhook_signature(raw_rzp_bytes)
    parsed_rzp = rzp_adapter.parse_webhook_event(raw_rzp_bytes, sig_rzp)
    if parsed_rzp.get("tenant_id") != "":
        violations.append(
            f"Razorpay parser emitted '{parsed_rzp.get('tenant_id')}' instead of empty string when notes omit tenant"
        )
    if "tenant_01" in str(parsed_rzp.get("tenant_id")):
        violations.append("Razorpay parser secretly fell back to 'tenant_01'!")

    stripe_adapter = StripePaymentProvider(api_key="sk_test_mock")
    raw_stripe_no_tenant = {
        "id": "evt_fallback_probe_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_fallback_probe_01",
                "amount": 100000,
                "currency": "INR",
                "status": "succeeded",
                "metadata": {"other": "value"},  # NO tenant_id
            }
        },
    }
    raw_stripe_bytes = json.dumps(raw_stripe_no_tenant, separators=(",", ":")).encode("utf-8")
    now_ts = int(time.time())
    sig_stripe = stripe_adapter.generate_webhook_signature(raw_stripe_bytes, now_ts)
    parsed_stripe = stripe_adapter.parse_webhook_event(
        raw_stripe_bytes, f"t={now_ts},v1={sig_stripe}"
    )
    if parsed_stripe.get("tenant_id") != "":
        violations.append(
            f"Stripe parser emitted '{parsed_stripe.get('tenant_id')}' instead of empty string when metadata omits tenant"
        )
    if "tenant_01" in str(parsed_stripe.get("tenant_id")):
        violations.append("Stripe parser secretly fell back to 'tenant_01'!")

    # 1.2 Dedicated Razorpay Webhook Ingress - Zero Fallback to tenant_01
    # Seed a real tenant_01 with active attempt and order
    _, client, env = _seed_environment(tenant_id="tenant_01")
    order_before = env["order_repo"].get("tenant_01", "ord_test_01")
    attempt_before = env["payment_repo"].get("tenant_01", "att_test_01")
    assert order_before.status == OrderStatus.PENDING_PAYMENT
    assert attempt_before.status == PaymentStatus.CREATED

    # Send a validly signed Razorpay webhook for an unmapped payment without tenant_id
    rzp_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_no_tenant_unmapped_01",
                    "amount": 149900,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {},
                }
            }
        },
    }
    resp_rzp = client.post_razorpay_webhook(rzp_payload)
    if resp_rzp.status_code != 422:
        violations.append(
            f"Razorpay webhook without tenant returned HTTP {resp_rzp.status_code}, expected 422 QUARANTINED"
        )
    if resp_rzp.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Razorpay webhook returned error code {resp_rzp.body.get('error', {}).get('code')}, expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )

    # Verify tenant_01 was NOT mutated
    order_after = env["order_repo"].get("tenant_01", "ord_test_01")
    attempt_after = env["payment_repo"].get("tenant_01", "att_test_01")
    if order_after.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"tenant_01 order was mutated to {order_after.status} by unassociated Razorpay webhook!"
        )
    if attempt_after.status != PaymentStatus.CREATED:
        violations.append(
            f"tenant_01 attempt was mutated to {attempt_after.status} by unassociated Razorpay webhook!"
        )

    # Verify no audit event was logged under tenant_01
    tenant_01_events = env["audit_ledger"].get_events("tenant_01")
    # All tenant_01 events should only be from setup, none from this webhook
    webhook_tenant_events = [
        e for e in tenant_01_events if "pay_no_tenant_unmapped_01" in str(e.data)
    ]
    if webhook_tenant_events:
        violations.append(
            f"Audit events leaked into tenant_01 for unassociated webhook: {webhook_tenant_events}"
        )

    # 1.3 Dedicated Stripe Webhook Ingress - Zero Fallback to tenant_01
    stripe_payload = {
        "id": "evt_stripe_no_tenant_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_no_tenant_unmapped_02",
                "amount": 149900,
                "currency": "INR",
                "status": "succeeded",
                "metadata": {},
            }
        },
    }
    resp_stripe = client.post_stripe_webhook(stripe_payload)
    if resp_stripe.status_code != 422:
        violations.append(
            f"Stripe webhook without tenant returned HTTP {resp_stripe.status_code}, expected 422 QUARANTINED"
        )
    if resp_stripe.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Stripe webhook returned error code {resp_stripe.body.get('error', {}).get('code')}, expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )

    order_after_stripe = env["order_repo"].get("tenant_01", "ord_test_01")
    attempt_after_stripe = env["payment_repo"].get("tenant_01", "att_test_01")
    if order_after_stripe.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"tenant_01 order was mutated to {order_after_stripe.status} by unassociated Stripe webhook!"
        )
    if attempt_after_stripe.status != PaymentStatus.CREATED:
        violations.append(
            f"tenant_01 attempt was mutated to {attempt_after_stripe.status} by unassociated Stripe webhook!"
        )

    # 1.4 Generic Webhook Ingress - Zero Fallback to tenant_01
    resp_generic = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_generic_no_tenant_03",
            "provider_payment_id": "pay_generic_unmapped_03",
        },
        tenant_id="",  # Explicitly omitted tenant
    )
    if resp_generic.status_code != 422:
        violations.append(
            f"Generic webhook without tenant returned HTTP {resp_generic.status_code}, expected 422 QUARANTINED"
        )
    if resp_generic.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Generic webhook returned error code {resp_generic.body.get('error', {}).get('code')}, expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )

    order_after_generic = env["order_repo"].get("tenant_01", "ord_test_01")
    attempt_after_generic = env["payment_repo"].get("tenant_01", "att_test_01")
    if order_after_generic.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"tenant_01 order was mutated to {order_after_generic.status} by unassociated generic webhook!"
        )
    if attempt_after_generic.status != PaymentStatus.CREATED:
        violations.append(
            f"tenant_01 attempt was mutated to {attempt_after_generic.status} by unassociated generic webhook!"
        )

    return {
        "probe": "Probe 1: Elimination of tenant_01 Fallback",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 2: Unmapped Provider Payment ID Quarantine
# ===========================================================================
def probe_unmapped_provider_payment_id_quarantine() -> dict[str, Any]:
    """Verify HTTP 422 UNRESOLVED_WEBHOOK_TRANSACTION, zero mutations, and SYSTEM security alert."""
    log("[->] Executing Probe 2 (Unmapped Provider Payment ID Quarantine)...")
    violations: list[str] = []

    _, client, env = _seed_environment(tenant_id="tenant_corp")
    inv_repo = env["inventory_repo"]
    pay_repo = env["payment_repo"]
    order_repo = env["order_repo"]
    audit_ledger = env["audit_ledger"]

    # 2.1 Razorpay with unmapped ID
    unmapped_rzp_id = "pay_unknown_rzp_999"
    rzp_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": unmapped_rzp_id,
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
    resp_rzp = client.post_razorpay_webhook(rzp_payload)
    if resp_rzp.status_code != 422:
        violations.append(
            f"Unmapped Razorpay payment returned HTTP {resp_rzp.status_code}, expected 422"
        )
    if resp_rzp.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Unmapped Razorpay payment code was '{resp_rzp.body.get('error', {}).get('code')}', expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )
    if resp_rzp.body.get("error", {}).get("details", {}).get("status") != "QUARANTINED":
        violations.append("Unmapped Razorpay details.status was not QUARANTINED")

    # 2.2 Stripe with unmapped ID
    unmapped_stripe_id = "pi_unknown_stripe_888"
    stripe_payload = {
        "id": "evt_stripe_unmapped_test",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": unmapped_stripe_id,
                "amount": 50000,
                "currency": "INR",
                "status": "succeeded",
            }
        },
    }
    resp_stripe = client.post_stripe_webhook(stripe_payload)
    if resp_stripe.status_code != 422:
        violations.append(
            f"Unmapped Stripe payment returned HTTP {resp_stripe.status_code}, expected 422"
        )
    if resp_stripe.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Unmapped Stripe payment code was '{resp_stripe.body.get('error', {}).get('code')}', expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )
    if resp_stripe.body.get("error", {}).get("details", {}).get("status") != "QUARANTINED":
        violations.append("Unmapped Stripe details.status was not QUARANTINED")

    # 2.3 Generic with unmapped ID
    unmapped_generic_id = "pay_unknown_generic_777"
    resp_generic = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_generic_unmapped_test",
            "provider_payment_id": unmapped_generic_id,
        },
        tenant_id="",
    )
    if resp_generic.status_code != 422:
        violations.append(
            f"Unmapped generic payment returned HTTP {resp_generic.status_code}, expected 422"
        )
    if resp_generic.body.get("error", {}).get("code") != "UNRESOLVED_WEBHOOK_TRANSACTION":
        violations.append(
            f"Unmapped generic payment code was '{resp_generic.body.get('error', {}).get('code')}', expected UNRESOLVED_WEBHOOK_TRANSACTION"
        )

    # 2.4 Verify Zero Mutations across orders, payments, and inventory
    order = order_repo.get("tenant_corp", "ord_test_01")
    attempt = pay_repo.get("tenant_corp", "att_test_01")
    inv = inv_repo.get("tenant_corp", "var_01")
    if order.status != OrderStatus.PENDING_PAYMENT:
        violations.append(f"Order status was mutated to {order.status} despite quarantine!")
    if attempt.status != PaymentStatus.CREATED:
        violations.append(
            f"PaymentAttempt status was mutated to {attempt.status} despite quarantine!"
        )
    if inv.available != 10 or inv.reserved != 2:
        violations.append(
            f"Inventory was mutated (avail={inv.available}, res={inv.reserved}) despite quarantine!"
        )

    # 2.5 Verify Security Alert Recorded in SYSTEM Tenant
    system_events = audit_ledger.get_events("SYSTEM")
    sec_alerts = [e for e in system_events if e.event_type == AuditEventType.SECURITY_ALERT]
    if len(sec_alerts) < 3:
        violations.append(
            f"Expected at least 3 SECURITY_ALERT events in SYSTEM tenant, found {len(sec_alerts)}"
        )
    for alert in sec_alerts:
        if alert.data.get("reason") != "UNRESOLVED_WEBHOOK_TRANSACTION":
            violations.append(
                f"SYSTEM alert reason was '{alert.data.get('reason')}', expected UNRESOLVED_WEBHOOK_TRANSACTION"
            )
        if alert.actor != "webhook_quarantine":
            violations.append(
                f"SYSTEM alert actor was '{alert.actor}', expected 'webhook_quarantine'"
            )

    # 2.6 Concurrency Quarantine Burst Probe (20 parallel requests)
    log("    -> Stressing quarantine under concurrent multithreaded burst (20 threads)...")
    concurrent_violations: list[str] = []

    def send_unmapped_burst(thread_idx: int) -> int:
        p_id = f"pay_burst_unmapped_{thread_idx}"
        p_load = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": p_id,
                        "amount": 10000 + thread_idx,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
        r = client.post_razorpay_webhook(p_load)
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(send_unmapped_burst, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    non_422 = [sc for sc in results if sc != 422]
    if non_422:
        concurrent_violations.append(f"Concurrent burst had non-422 responses: {non_422}")

    violations.extend(concurrent_violations)

    return {
        "probe": "Probe 2: Unmapped Provider Payment ID Quarantine",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 3: Cross-Tenant Spoofing
# ===========================================================================
def probe_cross_tenant_spoofing() -> dict[str, Any]:
    """Verify rejection of forged tenant headers or metadata pointing to another tenant's mapping."""
    log("[->] Executing Probe 3 (Cross-Tenant Spoofing)...")
    violations: list[str] = []

    _app, client, env = _seed_environment(tenant_id="tenant_alpha")
    mapping_repo = env["provider_mapping_repo"]
    pay_repo = env["payment_repo"]
    order_repo = env["order_repo"]

    # 3.1 Register legitimate transaction mapping belonging to Tenant A (tenant_alpha)
    mapping_alpha = ProviderTransactionMapping(
        mapping_id="map_alpha_tx_01",
        tenant_id="tenant_alpha",
        attempt_id="att_test_01",
        provider="razorpay",
        provider_payment_id="pay_alpha_tx_001",
    )
    mapping_repo.save(mapping_alpha)

    # Attacker asserts Tenant B (tenant_bravo) via X-Tenant-Id header for Tenant A's transaction
    rzp_attack_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_alpha_tx_001",
                    "amount": 149900,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"tenant_id": "tenant_bravo"},  # Hostile tenant injection in notes
                }
            }
        },
    }
    resp_hdr_attack = client.post_razorpay_webhook(
        rzp_attack_payload,
        headers={"x-tenant-id": "tenant_bravo"},  # Hostile tenant injection in header
    )
    if resp_hdr_attack.status_code != 400:
        violations.append(
            f"Cross-tenant header spoofing returned HTTP {resp_hdr_attack.status_code}, expected 400"
        )
    err_code = resp_hdr_attack.body.get("error", {}).get("code", "")
    err_msg = str(resp_hdr_attack.body.get("error", {}).get("message", ""))
    if "CROSS_TENANT" not in err_code and "CrossTenantAccessError" not in err_msg:
        violations.append(
            f"Cross-tenant header spoofing did not raise CrossTenantAccessError: {resp_hdr_attack.body}"
        )

    # 3.2 Verify Tenant A's state is completely unmutated
    order_alpha = order_repo.get("tenant_alpha", "ord_test_01")
    attempt_alpha = pay_repo.get("tenant_alpha", "att_test_01")
    if order_alpha.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"Tenant A order was mutated to {order_alpha.status} during cross-tenant attack!"
        )
    if attempt_alpha.status != PaymentStatus.CREATED:
        violations.append(
            f"Tenant A attempt was mutated to {attempt_alpha.status} during cross-tenant attack!"
        )

    # 3.3 Verify Tenant B has no records created or altered
    order_bravo = order_repo.get("tenant_bravo", "ord_test_01")
    attempt_bravo = pay_repo.get("tenant_bravo", "att_test_01")
    if order_bravo is not None:
        violations.append("Ghost order was created in tenant_bravo!")
    if attempt_bravo is not None:
        violations.append("Ghost payment attempt was created in tenant_bravo!")

    # 3.4 Stripe Cross-Tenant Injection Probe
    mapping_stripe_alpha = ProviderTransactionMapping(
        mapping_id="map_stripe_alpha_01",
        tenant_id="tenant_alpha",
        attempt_id="att_test_01",
        provider="stripe",
        provider_payment_id="pi_alpha_stripe_001",
    )
    mapping_repo.save(mapping_stripe_alpha)

    stripe_attack_payload = {
        "id": "evt_stripe_cross_tenant_attack",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_alpha_stripe_001",
                "amount": 149900,
                "currency": "INR",
                "status": "succeeded",
                "metadata": {"tenant_id": "tenant_bravo"},  # Conflicting tenant in metadata
            }
        },
    }
    resp_stripe_attack = client.post_stripe_webhook(
        stripe_attack_payload,
        headers={"x-tenant-id": "tenant_bravo"},
    )
    if resp_stripe_attack.status_code != 400:
        violations.append(
            f"Stripe cross-tenant spoofing returned HTTP {resp_stripe_attack.status_code}, expected 400"
        )
    err_code_s = resp_stripe_attack.body.get("error", {}).get("code", "")
    err_msg_s = str(resp_stripe_attack.body.get("error", {}).get("message", ""))
    if "CROSS_TENANT" not in err_code_s and "CrossTenantAccessError" not in err_msg_s:
        violations.append(
            f"Stripe cross-tenant spoofing did not raise CrossTenantAccessError: {resp_stripe_attack.body}"
        )

    # Verify again that Tenant A's state remains intact
    order_alpha_2 = order_repo.get("tenant_alpha", "ord_test_01")
    if order_alpha_2.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"Tenant A order mutated to {order_alpha_2.status} during Stripe cross-tenant attack!"
        )

    # 3.5 Attempt ID Hijacking across tenants (Attacker asserts Tenant B with Tenant A's attempt ID)
    resp_hijack = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_hijack_attempt_01",
            "attempt_id": "att_test_01",  # Belongs to Tenant A
            "provider_payment_id": "prov_hijack_001",
        },
        tenant_id="tenant_bravo",
    )
    if resp_hijack.status_code not in (400, 422):
        violations.append(
            f"Attempt ID hijacking across tenants returned HTTP {resp_hijack.status_code}, expected 400 or 422"
        )
    if "CrossTenantAccessError" not in str(resp_hijack.body) and "not found" not in str(
        resp_hijack.body
    ):
        violations.append(f"Hijack attempt did not fail closed: {resp_hijack.body}")

    return {
        "probe": "Probe 3: Cross-Tenant Spoofing",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 4: Stored Mapping Correctness
# ===========================================================================
def probe_stored_mapping_correctness() -> dict[str, Any]:
    """Verify legitimate purchase orchestration creates durable mapping and unannotated webhooks succeed."""
    log("[->] Executing Probe 4 (Stored Mapping Correctness & Multi-Tenant Isolation)...")
    violations: list[str] = []

    # 4.1 Setup Tenant Alpha and Tenant Beta with RazorpayPaymentProvider
    rzp_alpha = RazorpayPaymentProvider(
        key_id="rzp_test_alpha_k",
        key_secret="rzp_test_alpha_s",
        auto_capture=False,
    )
    _app_alpha, client_alpha, env_alpha = _seed_environment(
        tenant_id="tenant_alpha",
        attempt_id="att_alpha_real",
        order_id="ord_alpha_real",
        payment_provider=rzp_alpha,
    )
    orch_alpha = env_alpha["orchestrator"]
    mapping_repo = env_alpha["provider_mapping_repo"]

    # Seed Tenant Beta as isolation control
    _, _, env_beta = _seed_environment(
        tenant_id="tenant_beta",
        attempt_id="att_beta_real",
        order_id="ord_beta_real",
    )
    # Share the underlying repos into the orchestrator so both tenants exist concurrently
    env_alpha["payment_repo"].save("tenant_beta", env_beta["attempt"])
    env_alpha["order_repo"].save("tenant_beta", env_beta["order"])
    env_alpha["inventory_repo"].save("tenant_beta", env_beta["inventory"])

    # 4.2 Execute a real purchase through PurchaseOrchestrator in Tenant Alpha
    req = ActionRequest(
        action_id=f"act_orch_probe_{int(time.time())}",
        tenant_id="tenant_alpha",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product_variant", id="var_01"),
        amount=Money(129900, "INR"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="Autonomous probe item", budget_max=Money(200000, "INR")),
        authorization_mode=AuthorizationMode.AUTONOMOUS,
    )
    orch_res = orch_alpha.execute_purchase(
        action=req,
        tenant_id="tenant_alpha",
        variant_id="var_01",
        quantity=1,
    )
    if (
        orch_res.status != ActionStatus.COMPLETED
        or orch_res.order is None
        or orch_res.payment is None
    ):
        violations.append(
            f"PurchaseOrchestrator failed to execute purchase: status={orch_res.status}, reason={orch_res.rejection_reason}"
        )
        return {
            "probe": "Probe 4: Stored Mapping Correctness",
            "passed": False,
            "violations": violations,
        }

    real_attempt_id = orch_res.payment.attempt_id
    real_order_id = orch_res.order.order_id
    assert real_attempt_id is not None
    assert real_order_id is not None

    # Verify that PurchaseOrchestrator persisted a ProviderTransactionMapping
    stored_mapping = mapping_repo.find_by_attempt_id("tenant_alpha", real_attempt_id)
    if stored_mapping is None:
        violations.append(
            f"PurchaseOrchestrator did NOT persist ProviderTransactionMapping for attempt '{real_attempt_id}'!"
        )
    else:
        if stored_mapping.tenant_id != "tenant_alpha":
            violations.append(
                f"Stored mapping tenant is '{stored_mapping.tenant_id}', expected 'tenant_alpha'"
            )
        if stored_mapping.attempt_id != real_attempt_id:
            violations.append(
                f"Stored mapping attempt_id is '{stored_mapping.attempt_id}', expected '{real_attempt_id}'"
            )

    prov_tx_id = stored_mapping.provider_payment_id if stored_mapping else real_attempt_id

    # Reset attempt to CREATED and order to PENDING_PAYMENT to test asynchronous webhook transition
    initial_attempt = env_alpha["payment_repo"].get("tenant_alpha", real_attempt_id)
    assert initial_attempt is not None
    created_attempt = PaymentAttempt(
        attempt_id=initial_attempt.attempt_id,
        intent_id=initial_attempt.intent_id,
        tenant_id=initial_attempt.tenant_id,
        amount=initial_attempt.amount,
        status=PaymentStatus.CREATED,
        idempotency_key=initial_attempt.idempotency_key,
        provider=initial_attempt.provider,
        provider_payment_id=initial_attempt.provider_payment_id,
    )
    env_alpha["payment_repo"].save("tenant_alpha", created_attempt)

    initial_order = env_alpha["order_repo"].get("tenant_alpha", real_order_id)
    assert initial_order is not None
    pending_order = Order(
        order_id=initial_order.order_id,
        tenant_id=initial_order.tenant_id,
        merchant_id=initial_order.merchant_id,
        buyer_id=initial_order.buyer_id,
        agent_id=initial_order.agent_id,
        variant_id=initial_order.variant_id,
        quantity=initial_order.quantity,
        total_amount=initial_order.total_amount,
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id=initial_order.payment_attempt_id,
        created_at=initial_order.created_at,
    )
    env_alpha["order_repo"].save("tenant_alpha", pending_order)

    # 4.3 Deliver Razorpay webhook for this transaction without ANY tenant header or notes
    rzp_valid_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": prov_tx_id,
                    "amount": 129900,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {},  # NO tenant_id! Strictly relies on mapping!
                }
            }
        },
    }
    resp_rzp = client_alpha.post_razorpay_webhook(rzp_valid_payload)
    if resp_rzp.status_code != 200:
        violations.append(
            f"Razorpay webhook with stored mapping returned HTTP {resp_rzp.status_code}, expected 200. Body: {resp_rzp.body}"
        )
    if resp_rzp.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay webhook status was '{resp_rzp.body.get('status')}', expected 'PROCESSED'"
        )
    if resp_rzp.body.get("tenant_id") != "tenant_alpha":
        violations.append(
            f"Razorpay webhook resolved tenant was '{resp_rzp.body.get('tenant_id')}', expected 'tenant_alpha'"
        )

    # Verify Tenant Alpha's attempt & order transitioned to CAPTURED / CONFIRMED
    updated_attempt = env_alpha["payment_repo"].get("tenant_alpha", real_attempt_id)
    updated_order = env_alpha["order_repo"].get("tenant_alpha", real_order_id)
    if updated_attempt.status != PaymentStatus.CAPTURED:
        violations.append(
            f"Tenant Alpha payment attempt status is {updated_attempt.status}, expected CAPTURED"
        )
    if updated_order.status != OrderStatus.CONFIRMED:
        violations.append(
            f"Tenant Alpha order status is {updated_order.status}, expected CONFIRMED"
        )

    # 4.4 Deliver Stripe webhook for another legitimate mapped transaction in Tenant Alpha
    stripe_attempt_id = "att_stripe_mapped_01"
    stripe_order_id = "ord_stripe_mapped_01"
    stripe_prov_tx = "pi_stripe_legit_mapped_777"

    env_alpha["payment_repo"].save(
        "tenant_alpha",
        PaymentAttempt(
            attempt_id=stripe_attempt_id,
            intent_id="intent_stripe_01",
            tenant_id="tenant_alpha",
            amount=Money(149900, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_stripe_01",
            provider="stripe",
            provider_payment_id=stripe_prov_tx,
        ),
    )
    env_alpha["order_repo"].save(
        "tenant_alpha",
        Order(
            order_id=stripe_order_id,
            tenant_id="tenant_alpha",
            merchant_id="merch_01",
            buyer_id="user_01",
            agent_id="agent_01",
            variant_id="var_01",
            quantity=2,
            total_amount=Money(149900, "INR"),
            status=OrderStatus.PENDING_PAYMENT,
            payment_attempt_id=stripe_attempt_id,
        ),
    )
    mapping_repo.save(
        ProviderTransactionMapping(
            mapping_id="map_stripe_legit_01",
            tenant_id="tenant_alpha",
            attempt_id=stripe_attempt_id,
            provider="stripe",
            provider_payment_id=stripe_prov_tx,
        )
    )

    stripe_valid_payload = {
        "id": "evt_stripe_legit_webhook_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": stripe_prov_tx,
                "amount": 149900,
                "currency": "INR",
                "status": "succeeded",
                "metadata": {},  # NO tenant_id! Strictly relies on mapping!
            }
        },
    }
    resp_stripe = client_alpha.post_stripe_webhook(stripe_valid_payload)
    if resp_stripe.status_code != 200:
        violations.append(
            f"Stripe webhook with stored mapping returned HTTP {resp_stripe.status_code}, expected 200. Body: {resp_stripe.body}"
        )
    if resp_stripe.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe webhook status was '{resp_stripe.body.get('status')}', expected 'PROCESSED'"
        )
    if resp_stripe.body.get("tenant_id") != "tenant_alpha":
        violations.append(
            f"Stripe webhook resolved tenant was '{resp_stripe.body.get('tenant_id')}', expected 'tenant_alpha'"
        )

    updated_stripe_attempt = env_alpha["payment_repo"].get("tenant_alpha", stripe_attempt_id)
    updated_stripe_order = env_alpha["order_repo"].get("tenant_alpha", stripe_order_id)
    if updated_stripe_attempt.status != PaymentStatus.CAPTURED:
        violations.append(
            f"Tenant Alpha Stripe attempt status is {updated_stripe_attempt.status}, expected CAPTURED"
        )
    if updated_stripe_order.status != OrderStatus.CONFIRMED:
        violations.append(
            f"Tenant Alpha Stripe order status is {updated_stripe_order.status}, expected CONFIRMED"
        )

    # 4.5 Verify Zero Side Effects on Tenant Beta
    beta_attempt = env_alpha["payment_repo"].get("tenant_beta", "att_beta_real")
    beta_order = env_alpha["order_repo"].get("tenant_beta", "ord_beta_real")
    if beta_attempt.status != PaymentStatus.CREATED:
        violations.append(
            f"Tenant Beta attempt was corrupted to {beta_attempt.status} during Tenant Alpha reconciliation!"
        )
    if beta_order.status != OrderStatus.PENDING_PAYMENT:
        violations.append(
            f"Tenant Beta order was corrupted to {beta_order.status} during Tenant Alpha reconciliation!"
        )

    return {
        "probe": "Probe 4: Stored Mapping Correctness",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Main Execution Runner
# ===========================================================================
def main() -> int:
    log("===========================================================================")
    log("Milestone 1 Empirical Stress Harness: Tenant Isolation & Mapping Integrity")
    log("Testing Invariants: AP-001, AP-002, AGENTS.md §49, §50, §51")
    log("===========================================================================")

    probes = [
        probe_elimination_of_tenant_01_fallback,
        probe_unmapped_provider_payment_id_quarantine,
        probe_cross_tenant_spoofing,
        probe_stored_mapping_correctness,
    ]

    results = []
    for p in probes:
        res = p()
        results.append(res)
        status = "PASSED" if res["passed"] else "FAILED"
        log(f"[{status}] {res['probe']}")
        if not res["passed"]:
            for v in res["violations"]:
                log(f"    - VIOLATION: {v}")

    log("---------------------------------------------------------------------------")
    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    log(f"Summary: {passed_count}/{total_count} empirical probes passed.")

    if passed_count == total_count:
        log(">>> VERDICT: ALL INVARIANTS SATISFIED -> APPROVE <<<")
        return 0
    else:
        log(">>> VERDICT: INVARIANT VIOLATIONS DETECTED -> REJECT <<<")
        return 1


if __name__ == "__main__":
    sys.exit(main())

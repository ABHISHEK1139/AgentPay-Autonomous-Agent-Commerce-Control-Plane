"""Milestone 1 Empirical Adversarial Cryptographic Verification Harness (§61, AP-001, AP-002).

Exhaustively verifies cryptographic and fail-closed contracts across payment webhook endpoints:
- Probe 1: Forged & Corrupted Signatures (bit flips, 1-byte mutations, empty/whitespace signatures, invalid hex on Razorpay and Stripe)
- Probe 2: Cross-Endpoint Signature Spoofing (Razorpay -> Stripe route, Stripe -> Razorpay route, header cross-pollution)
- Probe 3: Raw-Byte Integrity & Wire Fidelity (compact vs pretty JSON, whitespace mutations, key-ordering changes)
- Probe 4: Stripe Timestamp Freshness & Multi-Signature Secret Rotation (>300s past, >30s future, malformed ts, secret rotation)
- Probe 5: Razorpay Timestamp Neutrality & Replay Protection (omitted timestamp, stale timestamp, non-numeric timestamp ignored, replay rejected)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderTransactionMapping,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _seed_test_env(
    tenant_id: str = "tenant_01",
) -> tuple[AgentPayApiApp, ApiTestClient, dict[str, Any]]:
    """Seed test environment with explicit secrets, attempts, orders, and provider mappings."""
    orch, _, _, _, _, _, _, inventory_repo, _, audit_ledger, _ = setup_orchestrator(
        tenant_id=tenant_id
    )
    app = AgentPayApiApp(orchestrator=orch)
    app.razorpay_webhook_secret = "whsec_rzp_crypto_secret_32bytes_len"
    app.stripe_webhook_secret = "whsec_stripe_crypto_secret_32bytes"
    client = ApiTestClient(app)

    # 1. Razorpay mapped payment attempt & order
    rzp_attempt = PaymentAttempt(
        attempt_id="att_rzp_01",
        intent_id="intent_rzp_01",
        tenant_id=tenant_id,
        amount=Money(50000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_rzp_01",
        provider="razorpay",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save(tenant_id, rzp_attempt)

    rzp_order = Order(
        order_id="ord_rzp_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(50000, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_rzp_01",
    )
    assert orch._order_repo is not None
    orch._order_repo.save(tenant_id, rzp_order)

    mapping_rzp = ProviderTransactionMapping(
        mapping_id="map_rzp_01",
        tenant_id=tenant_id,
        attempt_id="att_rzp_01",
        provider="razorpay",
        provider_payment_id="pay_rzp_probe_01",
    )
    assert orch._provider_mapping_repo is not None
    orch._provider_mapping_repo.save(mapping_rzp)

    # 2. Stripe mapped payment attempt & order
    stripe_attempt = PaymentAttempt(
        attempt_id="att_stripe_01",
        intent_id="intent_stripe_01",
        tenant_id=tenant_id,
        amount=Money(75000, "USD"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_stripe_01",
        provider="stripe",
    )
    orch._payment_repo.save(tenant_id, stripe_attempt)

    stripe_order = Order(
        order_id="ord_stripe_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(75000, "USD"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_stripe_01",
    )
    orch._order_repo.save(tenant_id, stripe_order)

    mapping_stripe = ProviderTransactionMapping(
        mapping_id="map_stripe_01",
        tenant_id=tenant_id,
        attempt_id="att_stripe_01",
        provider="stripe",
        provider_payment_id="pi_stripe_probe_01",
    )
    orch._provider_mapping_repo.save(mapping_stripe)

    return (
        app,
        client,
        {
            "rzp_attempt": rzp_attempt,
            "rzp_order": rzp_order,
            "stripe_attempt": stripe_attempt,
            "stripe_order": stripe_order,
            "payment_repo": orch._payment_repo,
            "order_repo": orch._order_repo,
            "inventory_repo": inventory_repo,
            "audit_ledger": audit_ledger,
            "provider_mapping_repo": orch._provider_mapping_repo,
        },
    )


# ---------------------------------------------------------------------------
# PROBE 1: Forged & Corrupted Signatures
# ---------------------------------------------------------------------------
def probe_forged_and_corrupted_signatures() -> dict[str, Any]:
    """Test bit flips, 1-byte mutations, empty signatures, invalid hex strings on both endpoints.

    All must fail closed with HTTP 401 INVALID_WEBHOOK_SIGNATURE.
    """
    app, client, env = _seed_test_env()
    violations: list[str] = []

    rzp_secret = app.razorpay_webhook_secret
    stripe_secret = app.stripe_webhook_secret

    # Base payloads
    rzp_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_probe_01",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
    rzp_bytes = json.dumps(rzp_payload, separators=(",", ":")).encode("utf-8")
    rzp_valid_sig = hmac.new(rzp_secret.encode("utf-8"), rzp_bytes, hashlib.sha256).hexdigest()

    ts_now = int(time.time())
    stripe_payload = {
        "id": "evt_probe_stripe_forgery",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_probe_01",
                "amount": 75000,
                "currency": "USD",
                "status": "succeeded",
            }
        },
    }
    stripe_bytes = json.dumps(stripe_payload, separators=(",", ":")).encode("utf-8")
    stripe_signed_payload = f"{ts_now}.".encode() + stripe_bytes
    stripe_valid_sig = hmac.new(
        stripe_secret.encode("utf-8"), stripe_signed_payload, hashlib.sha256
    ).hexdigest()

    # --- Part A: Razorpay Corrupted & Forged Signatures ---
    rzp_tests = [
        ("Omitted signature", {"custom_signature": "__OMIT__"}),
        ("Blank signature", {"custom_signature": ""}),
        ("Whitespace signature", {"custom_signature": "   "}),
        (
            "Bit flip in signature",
            {"custom_signature": ("0" if rzp_valid_sig[0] != "0" else "1") + rzp_valid_sig[1:]},
        ),
        ("1-byte truncated hex (63 chars)", {"custom_signature": rzp_valid_sig[:-1]}),
        ("1-byte extended hex (65 chars)", {"custom_signature": rzp_valid_sig + "a"}),
        ("Invalid non-hex string", {"custom_signature": "z" * 64}),
        ("Non-hex special characters", {"custom_signature": "invalid!@#$%^&*()_+"}),
        ("Forged secret signature", {"secret": "wrong_attacker_secret_32bytes_len"}),
    ]

    for label, kwargs in rzp_tests:
        resp = client.post_razorpay_webhook(rzp_payload, **kwargs)
        if resp.status_code != 401:
            violations.append(f"Razorpay [{label}] returned HTTP {resp.status_code}, expected 401")
        err_code = resp.body.get("error", {}).get("code")
        if err_code != "INVALID_WEBHOOK_SIGNATURE":
            violations.append(
                f"Razorpay [{label}] error code was '{err_code}', expected 'INVALID_WEBHOOK_SIGNATURE'"
            )

    # --- Part B: Stripe Corrupted & Forged Signatures ---
    stripe_tests = [
        ("Omitted signature", {"custom_signature": "__OMIT__"}),
        ("Blank signature", {"custom_signature": ""}),
        ("Whitespace signature", {"custom_signature": "   "}),
        ("Missing timestamp header param", {"custom_signature": f"v1={stripe_valid_sig}"}),
        ("Missing v1 signature param", {"custom_signature": f"t={ts_now}"}),
        ("Empty v1 param", {"custom_signature": f"t={ts_now},v1="}),
        (
            "Bit flip in v1 signature",
            {
                "custom_signature": f"t={ts_now},v1={('0' if stripe_valid_sig[0] != '0' else '1') + stripe_valid_sig[1:]}"
            },
        ),
        (
            "1-byte truncated v1 hex (63 chars)",
            {"custom_signature": f"t={ts_now},v1={stripe_valid_sig[:-1]}"},
        ),
        (
            "1-byte extended v1 hex (65 chars)",
            {"custom_signature": f"t={ts_now},v1={stripe_valid_sig}f"},
        ),
        ("Invalid non-hex in v1", {"custom_signature": f"t={ts_now},v1={'z' * 64}"}),
        ("Forged secret in v1", {"secret": "wrong_attacker_secret_32bytes_len"}),
    ]

    for label, kwargs in stripe_tests:
        resp = client.post_stripe_webhook(stripe_payload, timestamp=ts_now, **kwargs)
        if resp.status_code != 401:
            violations.append(f"Stripe [{label}] returned HTTP {resp.status_code}, expected 401")
        err_code = resp.body.get("error", {}).get("code")
        if err_code != "INVALID_WEBHOOK_SIGNATURE":
            violations.append(
                f"Stripe [{label}] error code was '{err_code}', expected 'INVALID_WEBHOOK_SIGNATURE'"
            )

    # Invariant: Verify payment attempts and orders were NOT mutated by any failed probe
    rzp_att = env["payment_repo"].get("tenant_01", "att_rzp_01")
    if rzp_att is None or rzp_att.status != PaymentStatus.CREATED:
        violations.append(
            f"Razorpay attempt state was corrupted by forged signatures: {getattr(rzp_att, 'status', None)}"
        )
    str_att = env["payment_repo"].get("tenant_01", "att_stripe_01")
    if str_att is None or str_att.status != PaymentStatus.CREATED:
        violations.append(
            f"Stripe attempt state was corrupted by forged signatures: {getattr(str_att, 'status', None)}"
        )

    return {
        "probe": "probe_forged_and_corrupted_signatures",
        "passed": len(violations) == 0,
        "violations": violations,
        "tests_run": len(rzp_tests) + len(stripe_tests),
    }


# ---------------------------------------------------------------------------
# PROBE 2: Cross-Endpoint Signature Spoofing
# ---------------------------------------------------------------------------
def probe_cross_endpoint_signature_spoofing() -> dict[str, Any]:
    """Send Razorpay signed payload to Stripe route and vice versa.

    Ensure both fail closed with HTTP 401 INVALID_WEBHOOK_SIGNATURE.
    """
    app, client, _ = _seed_test_env()
    violations: list[str] = []

    ts_now = int(time.time())
    payload = {
        "id": "evt_cross_probe_01",
        "event": "payment.captured",
        "type": "payment_intent.succeeded",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_cross_01",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
        "data": {
            "object": {
                "id": "pi_cross_01",
                "amount": 50000,
                "currency": "INR",
                "status": "succeeded",
            }
        },
    }
    raw_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    rzp_sig = hmac.new(
        app.razorpay_webhook_secret.encode("utf-8"), raw_bytes, hashlib.sha256
    ).hexdigest()

    stripe_sig = hmac.new(
        app.stripe_webhook_secret.encode("utf-8"),
        f"{ts_now}.".encode() + raw_bytes,
        hashlib.sha256,
    ).hexdigest()
    stripe_header_val = f"t={ts_now},v1={stripe_sig}"

    # 1. Send Razorpay header to Stripe endpoint
    resp1 = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={"x-razorpay-signature": rzp_sig},
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp1.status_code != 401
        or resp1.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Razorpay header sent to Stripe endpoint returned {resp1.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 2. Send Stripe header to Razorpay endpoint
    resp2 = client.request(
        "POST",
        "/api/v1/webhooks/razorpay",
        headers={"stripe-signature": stripe_header_val},
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp2.status_code != 401
        or resp2.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe header sent to Razorpay endpoint returned {resp2.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 3. Send Razorpay raw hex in Stripe-Signature header (missing t= and v1=)
    resp3 = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={"stripe-signature": rzp_sig},
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp3.status_code != 401
        or resp3.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Raw Razorpay hex in Stripe-Signature returned {resp3.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 4. Formatted as Stripe-Signature: t={ts},v1={rzp_sig} to Stripe endpoint
    # (Fails because rzp_sig was computed over raw_bytes, not t.raw_bytes)
    resp4 = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={"stripe-signature": f"t={ts_now},v1={rzp_sig}"},
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp4.status_code != 401
        or resp4.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Razorpay sig wrapped in Stripe format returned {resp4.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 5. Formatted as X-Razorpay-Signature: {stripe_sig} to Razorpay endpoint
    # (Fails because stripe_sig was computed over t.raw_bytes, not raw_bytes)
    resp5 = client.request(
        "POST",
        "/api/v1/webhooks/razorpay",
        headers={"x-razorpay-signature": stripe_sig},
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp5.status_code != 401
        or resp5.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe v1 sig sent in X-Razorpay-Signature returned {resp5.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 6. Both headers present on Stripe route, but Stripe header is forged
    resp6 = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={
            "stripe-signature": f"t={ts_now},v1={'0' * 64}",
            "x-razorpay-signature": rzp_sig,
        },
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp6.status_code != 401
        or resp6.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe route with valid Razorpay header but forged Stripe header returned {resp6.status_code}, expected 401"
        )

    # 7. Both headers present on Razorpay route, but Razorpay header is forged
    resp7 = client.request(
        "POST",
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-signature": "0" * 64,
            "stripe-signature": stripe_header_val,
        },
        json_body=payload,
        auth=False,
        raw_body=raw_bytes,
    )
    if (
        resp7.status_code != 401
        or resp7.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Razorpay route with valid Stripe header but forged Razorpay header returned {resp7.status_code}, expected 401"
        )

    return {
        "probe": "probe_cross_endpoint_signature_spoofing",
        "passed": len(violations) == 0,
        "violations": violations,
        "tests_run": 7,
    }


# ---------------------------------------------------------------------------
# PROBE 3: Raw-Byte Integrity & Wire Fidelity
# ---------------------------------------------------------------------------
def probe_raw_byte_integrity() -> dict[str, Any]:
    """Test whitespace/formatting variations (compact vs pretty JSON, single space mutations).

    Confirm HMAC is strictly verified against incoming raw bytes, not parsed/re-serialized AST.
    """
    app, client, _ = _seed_test_env()
    violations: list[str] = []

    rzp_secret = app.razorpay_webhook_secret
    stripe_secret = app.stripe_webhook_secret

    # Base dictionary
    rzp_dict = {
        "event": "payment.captured",
        "id": "evt_raw_rzp_01",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_probe_01",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }

    compact_bytes = json.dumps(rzp_dict, separators=(",", ":")).encode("utf-8")

    compact_sig = hmac.new(rzp_secret.encode("utf-8"), compact_bytes, hashlib.sha256).hexdigest()

    # 1. Razorpay: Sign compact, send compact -> 200 PROCESSED
    r1 = client.post_razorpay_webhook(
        compact_bytes, custom_signature=compact_sig, raw_body=compact_bytes
    )
    if r1.status_code != 200 or r1.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay compact signed & compact sent returned {r1.status_code}, expected 200 PROCESSED"
        )

    # 2. Razorpay: Sign pretty, send pretty -> 200 PROCESSED
    # Use different event ID to avoid replay conflict
    rzp_dict_pretty = dict(rzp_dict, id="evt_raw_rzp_pretty_01")
    pretty_bytes_2 = json.dumps(rzp_dict_pretty, indent=4).encode("utf-8")
    pretty_sig_2 = hmac.new(rzp_secret.encode("utf-8"), pretty_bytes_2, hashlib.sha256).hexdigest()

    r2 = client.post_razorpay_webhook(
        pretty_bytes_2, custom_signature=pretty_sig_2, raw_body=pretty_bytes_2
    )
    if r2.status_code != 200 or r2.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay pretty signed & pretty sent returned {r2.status_code}, expected 200 PROCESSED"
        )

    # 3. Razorpay: Sign compact, send pretty -> MUST FAIL with 401 INVALID_WEBHOOK_SIGNATURE
    rzp_dict_mismatch = dict(rzp_dict, id="evt_raw_rzp_mismatch_01")
    compact_b3 = json.dumps(rzp_dict_mismatch, separators=(",", ":")).encode("utf-8")
    pretty_b3 = json.dumps(rzp_dict_mismatch, indent=4).encode("utf-8")
    compact_sig_3 = hmac.new(rzp_secret.encode("utf-8"), compact_b3, hashlib.sha256).hexdigest()

    r3 = client.post_razorpay_webhook(pretty_b3, custom_signature=compact_sig_3, raw_body=pretty_b3)
    if r3.status_code != 401 or r3.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Razorpay compact signed but pretty sent returned {r3.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 4. Razorpay: Sign pretty, send compact -> MUST FAIL with 401 INVALID_WEBHOOK_SIGNATURE
    pretty_sig_3 = hmac.new(rzp_secret.encode("utf-8"), pretty_b3, hashlib.sha256).hexdigest()
    r4 = client.post_razorpay_webhook(
        compact_b3, custom_signature=pretty_sig_3, raw_body=compact_b3
    )
    if r4.status_code != 401 or r4.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Razorpay pretty signed but compact sent returned {r4.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 5. Razorpay: 1-byte whitespace alteration in transit
    rzp_dict_space = dict(rzp_dict, id="evt_raw_rzp_space_01")
    clean_bytes = json.dumps(rzp_dict_space, separators=(",", ":")).encode("utf-8")
    clean_sig = hmac.new(rzp_secret.encode("utf-8"), clean_bytes, hashlib.sha256).hexdigest()
    mutated_bytes = clean_bytes + b" "  # 1 trailing space byte

    r5 = client.post_razorpay_webhook(
        mutated_bytes, custom_signature=clean_sig, raw_body=mutated_bytes
    )
    if r5.status_code != 401 or r5.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Razorpay 1-byte trailing space in transit returned {r5.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 6. Razorpay: Internal space mutation '{"event": "payment.captured"}' vs '{"event":"payment.captured"}'
    mutated_internal_bytes = clean_bytes.replace(b'":', b'": ')
    r6 = client.post_razorpay_webhook(
        mutated_internal_bytes, custom_signature=clean_sig, raw_body=mutated_internal_bytes
    )
    if r6.status_code != 401 or r6.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Razorpay internal spacing mutation returned {r6.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 7. Razorpay: Key-ordering perturbation in wire bytes
    # JSON AST is identical, but wire octets differ
    key_order_1 = b'{"event":"payment.captured","id":"evt_order_01","type":"payment.captured"}'
    key_order_2 = b'{"id":"evt_order_01","event":"payment.captured","type":"payment.captured"}'
    order_sig_1 = hmac.new(rzp_secret.encode("utf-8"), key_order_1, hashlib.sha256).hexdigest()

    r7 = client.post_razorpay_webhook(
        key_order_2, custom_signature=order_sig_1, raw_body=key_order_2
    )
    if r7.status_code != 401 or r7.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Razorpay key order permutation returned {r7.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # --- Part B: Stripe Raw-Byte Integrity ---
    ts_now = int(time.time())
    stripe_dict = {
        "id": "evt_stripe_raw_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_probe_01",
                "amount": 75000,
                "currency": "USD",
                "status": "succeeded",
            }
        },
    }
    str_compact_bytes = json.dumps(stripe_dict, separators=(",", ":")).encode("utf-8")
    str_pretty_bytes = json.dumps(stripe_dict, indent=4).encode("utf-8")

    str_compact_sig = hmac.new(
        stripe_secret.encode("utf-8"),
        f"{ts_now}.".encode() + str_compact_bytes,
        hashlib.sha256,
    ).hexdigest()
    str_pretty_sig = hmac.new(
        stripe_secret.encode("utf-8"),
        f"{ts_now}.".encode() + str_pretty_bytes,
        hashlib.sha256,
    ).hexdigest()

    # 8. Stripe: Sign pretty, send pretty -> 200 PROCESSED
    r8 = client.post_stripe_webhook(
        str_pretty_bytes,
        timestamp=ts_now,
        custom_signature=f"t={ts_now},v1={str_pretty_sig}",
        raw_body=str_pretty_bytes,
    )
    if r8.status_code != 200 or r8.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe pretty signed & pretty sent returned {r8.status_code}, expected 200 PROCESSED"
        )

    # 9. Stripe: Sign compact, send pretty -> 401 INVALID_WEBHOOK_SIGNATURE
    stripe_dict_mismatch = dict(stripe_dict, id="evt_stripe_raw_mismatch_02")
    str_pretty_mismatch = json.dumps(stripe_dict_mismatch, indent=4).encode("utf-8")
    r9 = client.post_stripe_webhook(
        str_pretty_mismatch,
        timestamp=ts_now,
        custom_signature=f"t={ts_now},v1={str_compact_sig}",
        raw_body=str_pretty_mismatch,
    )
    if r9.status_code != 401 or r9.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE":
        violations.append(
            f"Stripe compact signed but pretty sent returned {r9.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 10. Stripe: 1-byte whitespace injection in transit
    stripe_dict_space = dict(stripe_dict, id="evt_stripe_space_03")
    clean_str_bytes = json.dumps(stripe_dict_space, separators=(",", ":")).encode("utf-8")
    clean_str_sig = hmac.new(
        stripe_secret.encode("utf-8"),
        f"{ts_now}.".encode() + clean_str_bytes,
        hashlib.sha256,
    ).hexdigest()
    mutated_str_bytes = clean_str_bytes + b"\n"

    r10 = client.post_stripe_webhook(
        mutated_str_bytes,
        timestamp=ts_now,
        custom_signature=f"t={ts_now},v1={clean_str_sig}",
        raw_body=mutated_str_bytes,
    )
    if (
        r10.status_code != 401
        or r10.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe 1-byte trailing newline returned {r10.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    return {
        "probe": "probe_raw_byte_integrity",
        "passed": len(violations) == 0,
        "violations": violations,
        "tests_run": 10,
    }


# ---------------------------------------------------------------------------
# PROBE 4: Stripe Timestamp Freshness & Multi-Signature Secret Rotation
# ---------------------------------------------------------------------------
def probe_stripe_timestamp_freshness_and_rotation() -> dict[str, Any]:
    """Test Stripe timestamps >300s old, future skew >30s, malformed timestamps, and secret rotation.

    Stale/future timestamps must fail closed with HTTP 401 EXPIRED_WEBHOOK_TIMESTAMP.
    Secret rotation must succeed when any valid v1 signature or configured candidate secret matches.
    """
    app, client, _ = _seed_test_env()
    violations: list[str] = []

    stripe_secret = app.stripe_webhook_secret

    base_payload = {
        "id": "evt_stripe_ts_probe_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_probe_01",
                "amount": 75000,
                "currency": "USD",
                "status": "succeeded",
            }
        },
    }

    def _sign_stripe(ts: int, payload_dict: dict[str, Any], secret: str) -> str:
        raw_b = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        return hmac.new(
            secret.encode("utf-8"), f"{ts}.".encode() + raw_b, hashlib.sha256
        ).hexdigest()

    # 1. Past expiration: 305s past (> 300s window) -> 401 EXPIRED_WEBHOOK_TIMESTAMP
    ts_305_past = int(time.time()) - 305
    p1 = dict(base_payload, id="evt_ts_305_past")
    sig1 = _sign_stripe(ts_305_past, p1, stripe_secret)
    r1 = client.post_stripe_webhook(
        p1, timestamp=ts_305_past, custom_signature=f"t={ts_305_past},v1={sig1}"
    )
    if r1.status_code != 401 or r1.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP":
        violations.append(
            f"Stripe timestamp 305s in past returned {r1.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 2. Far past expiration: 1 day past -> 401 EXPIRED_WEBHOOK_TIMESTAMP
    ts_day_past = int(time.time()) - 86400
    p2 = dict(base_payload, id="evt_ts_day_past")
    sig2 = _sign_stripe(ts_day_past, p2, stripe_secret)
    r2 = client.post_stripe_webhook(
        p2, timestamp=ts_day_past, custom_signature=f"t={ts_day_past},v1={sig2}"
    )
    if r2.status_code != 401 or r2.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP":
        violations.append(
            f"Stripe timestamp 1 day in past returned {r2.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 3. Future skew beyond tolerance: 35s in future (> 30s future skew tolerance)
    ts_35_future = int(time.time()) + 35
    p3 = dict(base_payload, id="evt_ts_35_future")
    sig3 = _sign_stripe(ts_35_future, p3, stripe_secret)
    r3 = client.post_stripe_webhook(
        p3, timestamp=ts_35_future, custom_signature=f"t={ts_35_future},v1={sig3}"
    )
    if r3.status_code != 401 or r3.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP":
        violations.append(
            f"Stripe future skew 35s returned {r3.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 4. Far future skew: 1 hour in future -> 401 EXPIRED_WEBHOOK_TIMESTAMP
    ts_hour_future = int(time.time()) + 3600
    p4 = dict(base_payload, id="evt_ts_hour_future")
    sig4 = _sign_stripe(ts_hour_future, p4, stripe_secret)
    r4 = client.post_stripe_webhook(
        p4, timestamp=ts_hour_future, custom_signature=f"t={ts_hour_future},v1={sig4}"
    )
    if r4.status_code != 401 or r4.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP":
        violations.append(
            f"Stripe future skew 1 hour returned {r4.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 5. Malformed non-numeric timestamp: t=bogus_ts -> 400 INVALID_WEBHOOK_TIMESTAMP
    r5 = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={
            "stripe-signature": "t=not_a_number_123,v1=any_valid_looking_hex_000000000000000000000000000000000000000000000000"
        },
        json_body=base_payload,
        auth=False,
    )
    if r5.status_code != 400 or r5.body.get("error", {}).get("code") != "INVALID_WEBHOOK_TIMESTAMP":
        violations.append(
            f"Stripe non-numeric timestamp returned {r5.status_code}, expected 400 INVALID_WEBHOOK_TIMESTAMP"
        )

    # 6. Valid past timestamp: 290s past (within 300s window) -> 200 PROCESSED
    ts_290_past = int(time.time()) - 290
    p6 = dict(base_payload, id="evt_ts_290_valid")
    sig6 = _sign_stripe(ts_290_past, p6, stripe_secret)
    r6 = client.post_stripe_webhook(
        p6, timestamp=ts_290_past, custom_signature=f"t={ts_290_past},v1={sig6}"
    )
    if r6.status_code != 200 or r6.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe valid timestamp 290s in past returned {r6.status_code}, expected 200 PROCESSED"
        )

    # 7. Valid future timestamp: 20s in future (within 30s future tolerance) -> 200 PROCESSED
    ts_20_future = int(time.time()) + 20
    p7 = dict(base_payload, id="evt_ts_20_valid")
    sig7 = _sign_stripe(ts_20_future, p7, stripe_secret)
    r7 = client.post_stripe_webhook(
        p7, timestamp=ts_20_future, custom_signature=f"t={ts_20_future},v1={sig7}"
    )
    if r7.status_code != 200 or r7.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe valid future timestamp 20s returned {r7.status_code}, expected 200 PROCESSED"
        )

    # 8. Secret rotation with multiple v1 signatures in header:
    # Header format: t={ts},v1={old_or_invalid_sig},v1={valid_sig}
    ts_rot = int(time.time()) - 5
    p8 = dict(base_payload, id="evt_stripe_multi_sig_01")
    sig8_valid = _sign_stripe(ts_rot, p8, stripe_secret)
    sig8_invalid = "0" * 64
    multi_v1_header = f"t={ts_rot},v1={sig8_invalid},v1={sig8_valid}"
    r8 = client.post_stripe_webhook(p8, timestamp=ts_rot, custom_signature=multi_v1_header)
    if r8.status_code != 200 or r8.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe multi-v1 signature header returned {r8.status_code}, expected 200 PROCESSED"
        )

    # 9. Server-side comma-separated secret rotation:
    # app.stripe_webhook_secret = "whsec_retired_key,whsec_active_key"
    app.stripe_webhook_secret = "whsec_retired_key_111,whsec_active_key_222"
    p9_retired = dict(base_payload, id="evt_stripe_rot_retired")
    sig9_retired = _sign_stripe(ts_rot, p9_retired, "whsec_retired_key_111")
    r9_ret = client.post_stripe_webhook(
        p9_retired,
        timestamp=ts_rot,
        custom_signature=f"t={ts_rot},v1={sig9_retired}",
    )
    if r9_ret.status_code != 200 or r9_ret.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe rotation with retired candidate key returned {r9_ret.status_code}, expected 200 PROCESSED"
        )

    p9_active = dict(base_payload, id="evt_stripe_rot_active")
    sig9_active = _sign_stripe(ts_rot, p9_active, "whsec_active_key_222")
    r9_act = client.post_stripe_webhook(
        p9_active,
        timestamp=ts_rot,
        custom_signature=f"t={ts_rot},v1={sig9_active}",
    )
    if r9_act.status_code != 200 or r9_act.body.get("status") != "PROCESSED":
        violations.append(
            f"Stripe rotation with active candidate key returned {r9_act.status_code}, expected 200 PROCESSED"
        )

    # 10. Server-side rotation fails when key is not in comma-separated list
    p10_bad = dict(base_payload, id="evt_stripe_rot_unknown")
    sig10_bad = _sign_stripe(ts_rot, p10_bad, "whsec_unknown_key_999")
    r10 = client.post_stripe_webhook(
        p10_bad,
        timestamp=ts_rot,
        custom_signature=f"t={ts_rot},v1={sig10_bad}",
    )
    if (
        r10.status_code != 401
        or r10.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe rotation with unknown key returned {r10.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 11. Multi-v1 header where ALL v1 signatures are invalid
    p11_all_bad = dict(base_payload, id="evt_stripe_multi_bad")
    multi_v1_all_bad = f"t={ts_rot},v1={'1' * 64},v1={'2' * 64},v1={'3' * 64}"
    r11 = client.post_stripe_webhook(
        p11_all_bad,
        timestamp=ts_rot,
        custom_signature=multi_v1_all_bad,
    )
    if (
        r11.status_code != 401
        or r11.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Stripe multi-v1 with all invalid signatures returned {r11.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    return {
        "probe": "probe_stripe_timestamp_freshness_and_rotation",
        "passed": len(violations) == 0,
        "violations": violations,
        "tests_run": 11,
    }


# ---------------------------------------------------------------------------
# PROBE 5: Razorpay Timestamp Neutrality & Replay Protection
# ---------------------------------------------------------------------------
def probe_razorpay_timestamp_neutrality() -> dict[str, Any]:
    """Verify Razorpay route succeeds without requiring any timestamp header, and ignores stale timestamps.

    Also verifies that replay defense remains active for duplicate delivery of the same event ID.
    """
    app, client, _ = _seed_test_env()
    violations: list[str] = []

    rzp_secret = app.razorpay_webhook_secret

    base_payload = {
        "event": "payment.captured",
        "id": "evt_rzp_neutral_01",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_probe_01",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }

    # 1. Successful verification with completely omitted timestamp header
    r1 = client.post_razorpay_webhook(base_payload, secret=rzp_secret)
    if r1.status_code != 200 or r1.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay omitted timestamp header returned {r1.status_code}, expected 200 PROCESSED"
        )

    # 2. Replay of identical event_id must fail closed with 409 WEBHOOK_REPLAY_DETECTED
    r2 = client.post_razorpay_webhook(base_payload, secret=rzp_secret)
    if r2.status_code != 409 or r2.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
        violations.append(
            f"Razorpay duplicate event delivery returned {r2.status_code}, expected 409 WEBHOOK_REPLAY_DETECTED"
        )

    # 3. Successful verification even if caller supplies a timestamp from 10 days ago
    p3 = dict(base_payload, id="evt_rzp_neutral_stale_ts")
    stale_ts = str(int(time.time()) - 864000)  # 10 days in past
    r3 = client.post_razorpay_webhook(
        p3,
        secret=rzp_secret,
        headers={"x-webhook-timestamp": stale_ts},
    )
    if r3.status_code != 200 or r3.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay with 10-day stale x-webhook-timestamp returned {r3.status_code}, expected 200 PROCESSED"
        )

    # 4. Successful verification even if caller supplies a future timestamp (10 days ahead)
    p4 = dict(base_payload, id="evt_rzp_neutral_future_ts")
    future_ts = str(int(time.time()) + 864000)
    r4 = client.post_razorpay_webhook(
        p4,
        secret=rzp_secret,
        headers={"x-webhook-timestamp": future_ts},
    )
    if r4.status_code != 200 or r4.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay with future x-webhook-timestamp returned {r4.status_code}, expected 200 PROCESSED"
        )

    # 5. Successful verification even if caller supplies a non-numeric timestamp header
    p5 = dict(base_payload, id="evt_rzp_neutral_garbage_ts")
    r5 = client.post_razorpay_webhook(
        p5,
        secret=rzp_secret,
        headers={"x-webhook-timestamp": "not_an_epoch_timestamp_at_all"},
    )
    if r5.status_code != 200 or r5.body.get("status") != "PROCESSED":
        violations.append(
            f"Razorpay with non-numeric x-webhook-timestamp returned {r5.status_code}, expected 200 PROCESSED"
        )

    return {
        "probe": "probe_razorpay_timestamp_neutrality",
        "passed": len(violations) == 0,
        "violations": violations,
        "tests_run": 5,
    }


# ---------------------------------------------------------------------------
# MAIN ADVERSARIAL HARNESS RUNNER
# ---------------------------------------------------------------------------
def main() -> int:
    log("=" * 72)
    log("AGENTPAY MILESTONE 1 (AP-001/AP-002) ADVERSARIAL CRYPTOGRAPHIC HARNESS")
    log("=" * 72)

    probes = [
        (
            "Probe 1: Forged & Corrupted Signatures",
            probe_forged_and_corrupted_signatures,
        ),
        (
            "Probe 2: Cross-Endpoint Signature Spoofing",
            probe_cross_endpoint_signature_spoofing,
        ),
        (
            "Probe 3: Raw-Byte Integrity & Wire Fidelity",
            probe_raw_byte_integrity,
        ),
        (
            "Probe 4: Stripe Timestamp Freshness & Rotation",
            probe_stripe_timestamp_freshness_and_rotation,
        ),
        (
            "Probe 5: Razorpay Timestamp Neutrality & Replay",
            probe_razorpay_timestamp_neutrality,
        ),
    ]

    all_passed = True
    total_subtests = 0

    for name, fn in probes:
        res = fn()
        subtests = res.get("tests_run", 0)
        total_subtests += subtests
        if res.get("passed"):
            log(f"[*] {name} ({subtests} sub-tests): PASS")
        else:
            log(f"[!] {name} ({subtests} sub-tests): FAIL -> {res.get('violations')}")
            all_passed = False

    log("=" * 72)
    log(f"TOTAL ADVERSARIAL CRYPTOGRAPHIC PROBES EXECUTED: {total_subtests}")
    if all_passed:
        log("[+] FINAL VERDICT: ALL INVARIANTS SATISFIED -> APPROVE")
        return 0
    else:
        log("[-] FINAL VERDICT: VULNERABILITIES DETECTED -> REJECT")
        return 1


if __name__ == "__main__":
    sys.exit(main())

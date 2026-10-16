"""Empirical Challenger Test Suite for Payment Webhook Ingestion & Replay Defense (§61, R3).

Adversarially probes:
1. Forged HMAC-SHA256 signature rejection (HTTP 401 INVALID_WEBHOOK_SIGNATURE).
2. Missing or empty signature rejection (HTTP 401 INVALID_WEBHOOK_SIGNATURE).
3. Expired timestamp (>300 seconds) rejection (HTTP 401 EXPIRED_WEBHOOK_TIMESTAMP).
4. Future timestamp outside window rejection (HTTP 401 EXPIRED_WEBHOOK_TIMESTAMP).
5. Duplicate replay detection & 20-thread concurrency race condition (HTTP 409 WEBHOOK_REPLAY_DETECTED).
6. Cross-tenant payment access fails closed without mutation (HTTP 400 INVALID_PAYMENT).
7. SAGA compensation & audit log integrity under webhook failure.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import time
from typing import Any

from test_purchase_flow import setup_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.audit import AuditEventType
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)


def _seed_test_env(
    tenant_id: str = "tenant_01",
) -> tuple[AgentPayApiApp, ApiTestClient, dict[str, Any]]:
    orch, _, _, _, _, _, _, inventory_repo, _, audit_ledger, _ = setup_orchestrator(
        tenant_id=tenant_id
    )
    app = AgentPayApiApp(orchestrator=orch, webhook_secret="whsec_unit_test_only_secret_32bytes")
    client = ApiTestClient(app)

    # Seed PaymentAttempt
    attempt = PaymentAttempt(
        attempt_id="att_test_challenger_01",
        intent_id="intent_test_01",
        tenant_id=tenant_id,
        amount=Money(129900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_01",
        provider="mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save(tenant_id, attempt)

    # Seed Order
    order = Order(
        order_id="ord_test_challenger_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(129900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_test_challenger_01",
    )
    assert orch._order_repo is not None
    orch._order_repo.save(tenant_id, order)

    return (
        app,
        client,
        {
            "attempt": attempt,
            "order": order,
            "payment_repo": orch._payment_repo,
            "order_repo": orch._order_repo,
            "inventory_repo": inventory_repo,
            "audit_ledger": audit_ledger,
        },
    )


def test_challenger_probe_1_forged_signatures() -> None:
    """Probe 1: Adversarial forged signature variants are rejected with HTTP 401."""
    _, client, _ = _seed_test_env()

    cases: list[tuple[str, str]] = [
        ("bogus_hex", "0" * 64),
        ("wrong_secret", "whsec_wrong_attacker_secret_key_12345678"),
        ("truncated", "abcdef1234567890abcdef1234567890"),
        ("non_hex", "ZZZZ" * 16),
        ("wrong_prefix", "sha512=" + "a" * 64),
    ]

    for label, sig_val in cases:
        payload = {
            "event_id": f"evt_forge_{label}",
            "event_type": "payment.captured",
            "attempt_id": "att_test_challenger_01",
        }
        if sig_val.startswith("whsec_"):
            resp = client.post_webhook("payment.captured", payload, secret=sig_val)
        else:
            resp = client.post_webhook("payment.captured", payload, custom_signature=sig_val)
        assert resp.status_code == 401
        assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"

    # Tampered payload after signature computation
    app, client, _ = _seed_test_env()
    secret = app.webhook_secret
    ts = int(time.time())
    legit_payload = {
        "event_id": "evt_tamper_body",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
        "amount": 100,
        "tenant_id": "tenant_01",
    }
    raw_json = json.dumps(legit_payload, separators=(",", ":"), sort_keys=True)
    valid_sig = hmac.new(secret.encode(), f"{ts}.{raw_json}".encode(), hashlib.sha256).hexdigest()

    tampered_payload = dict(legit_payload)
    tampered_payload["amount"] = 999999
    resp_tampered = client.request(
        "POST",
        "/api/v1/webhooks/payments",
        headers={
            "x-tenant-id": "tenant_01",
            "x-webhook-timestamp": str(ts),
            "x-webhook-signature": valid_sig,
        },
        json_body=tampered_payload,
        auth=False,
    )
    assert resp_tampered.status_code == 401
    assert resp_tampered.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_challenger_probe_2_missing_and_empty_signatures() -> None:
    """Probe 2: Missing or blank/empty signatures are rejected with HTTP 401."""
    _, client, _ = _seed_test_env()

    cases: list[str | None] = ["__OMIT__", "", "   ", "\t  \n  "]
    for sig_val in cases:
        payload = {
            "event_id": "evt_missing_sig",
            "event_type": "payment.captured",
            "attempt_id": "att_test_challenger_01",
        }
        resp = client.post_webhook("payment.captured", payload, custom_signature=sig_val)
        assert resp.status_code == 401
        assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_challenger_probe_3_expired_timestamps() -> None:
    """Probe 3: Webhooks older than 300s are rejected with HTTP 401 EXPIRED_WEBHOOK_TIMESTAMP."""
    _, client, _ = _seed_test_env()
    now_epoch = int(time.time())

    for delta_sec in [301, 350, 3600, 86400]:
        payload = {
            "event_id": f"evt_exp_{delta_sec}",
            "event_type": "payment.captured",
            "attempt_id": "att_test_challenger_01",
        }
        resp = client.post_webhook("payment.captured", payload, timestamp=now_epoch - delta_sec)
        assert resp.status_code == 401
        assert resp.body["error"]["code"] == "EXPIRED_WEBHOOK_TIMESTAMP"

    # Fresh timestamp within window (295s ago) should succeed
    payload_valid = {
        "event_id": "evt_valid_boundary",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
    }
    resp_fresh = client.post_webhook("payment.captured", payload_valid, timestamp=now_epoch - 295)
    assert resp_fresh.status_code == 200


def test_challenger_probe_4_future_timestamps() -> None:
    """Probe 4: Webhooks with timestamps > 300s in future are rejected."""
    _, client, _ = _seed_test_env()
    now_epoch = int(time.time())

    for delta_sec in [301, 350, 3600, 86400]:
        payload = {
            "event_id": f"evt_future_{delta_sec}",
            "event_type": "payment.captured",
            "attempt_id": "att_test_challenger_01",
        }
        resp = client.post_webhook("payment.captured", payload, timestamp=now_epoch + delta_sec)
        assert resp.status_code == 401
        assert resp.body["error"]["code"] == "EXPIRED_WEBHOOK_TIMESTAMP"

    # Tolerated clock skew (+10s ahead) should succeed
    payload_skew = {
        "event_id": "evt_skew_tolerated",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
    }
    resp_skew = client.post_webhook("payment.captured", payload_skew, timestamp=now_epoch + 10)
    assert resp_skew.status_code == 200


def test_challenger_probe_5_replay_defense_and_concurrency() -> None:
    """Probe 5: Replay duplicate detection & 20-thread concurrency race condition."""
    _, client, _ = _seed_test_env()

    payload = {
        "event_id": "evt_replay_challenger_99",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
        "provider_payment_id": "prov_cap_99",
    }

    # Initial delivery succeeds
    resp1 = client.post_webhook("payment.captured", payload)
    assert resp1.status_code == 200

    # Sequential duplicate delivery returns HTTP 409
    resp2 = client.post_webhook("payment.captured", payload)
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

    # Altered payload with same event_id also returns HTTP 409
    altered = dict(payload)
    altered["provider_payment_id"] = "prov_hijack_123"
    resp3 = client.post_webhook("payment.captured", altered)
    assert resp3.status_code == 409
    assert resp3.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

    # Concurrent 20-thread race condition
    _app_c, client_c, _ = _seed_test_env()
    conc_payload = {
        "event_id": "evt_race_challenger_20",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
    }

    def _send_concurrent() -> tuple[int, str]:
        r = client_c.post_webhook("payment.captured", conc_payload)
        code = r.body.get("status") or r.body.get("error", {}).get("code", "")
        return r.status_code, str(code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(_send_concurrent) for _ in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    status_200_count = sum(1 for status, _ in results if status == 200)
    status_409_count = sum(
        1 for status, code in results if status == 409 and code == "WEBHOOK_REPLAY_DETECTED"
    )
    assert status_200_count == 1
    assert status_409_count == 19


def test_challenger_probe_6_cross_tenant_isolation_fails_closed() -> None:
    """Probe 6: Cross-tenant payment access fails closed with HTTP 400 INVALID_PAYMENT."""
    _, client, env = _seed_test_env("tenant_01")

    payload = {
        "event_id": "evt_cross_probe",
        "event_type": "payment.captured",
        "attempt_id": "att_test_challenger_01",
    }

    # Cross-tenant attempt via header
    resp = client.post_webhook("payment.captured", payload, tenant_id="tenant_02")
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "INVALID_PAYMENT"

    # Verify tenant_01 payment attempt remains unmutated
    attempt = env["payment_repo"].get("tenant_01", "att_test_challenger_01")
    assert attempt is not None
    assert attempt.status == PaymentStatus.CREATED


def test_challenger_probe_7_saga_compensation_and_audit() -> None:
    """Probe 7: SAGA compensation on payment.failed and audit trail integrity."""
    _, client, env = _seed_test_env("tenant_01")

    # Reserve inventory first
    inv_repo = env["inventory_repo"]
    inv_repo.reserve("tenant_01", "var_01", 2)
    assert inv_repo.get("tenant_01", "var_01").reserved == 2

    # payment.failed releases inventory
    resp = client.post_webhook(
        "payment.failed",
        {
            "event_id": "evt_failed_saga",
            "event_type": "payment.failed",
            "attempt_id": "att_test_challenger_01",
            "reason": "Insufficient funds simulation",
        },
    )
    assert resp.status_code == 200
    assert resp.body["payment_status"] == "FAILED"
    assert resp.body["order_status"] == "CANCELLED"

    # Reserved inventory is 0
    inv = inv_repo.get("tenant_01", "var_01")
    assert inv.reserved == 0
    assert inv.effective_available == 10

    # Audit events created with actor="provider_webhook"
    events = env["audit_ledger"].get_events("tenant_01")
    failed_evts = [e for e in events if e.event_type == AuditEventType.PAYMENT_FAILED]
    assert len(failed_evts) >= 1
    assert failed_evts[0].actor == "provider_webhook"

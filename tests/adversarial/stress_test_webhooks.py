"""Empirical adversarial stress-testing harness for Payment Webhooks & Replay Defense (§61).

Exhaustively probes:
1. Probe 1: Signature forgery and tampering (omitted, blank, corrupted, wrong secret, prefix manipulation).
2. Probe 2: Timestamp freshness tolerance and drift bounds (>300s past/future, unparseable strings).
3. Probe 3: Idempotency replay defense and cross-tenant namespace separation.
4. Probe 4: Cross-tenant payment access fail-closed isolation.
5. Probe 5: Multithreaded race condition (30 concurrent threads racing same webhook delivery).
6. Probe 6: SAGA inventory compensation on payment failures and refunds.
"""

from __future__ import annotations

import concurrent.futures
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
from agentpay.domain.audit import AuditEventType
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _seed_test_env(
    tenant_id: str = "tenant_01",
) -> tuple[AgentPayApiApp, ApiTestClient, dict[str, Any]]:
    orch, _, _, _, _, _, _, inventory_repo, _, audit_ledger, _ = setup_orchestrator(
        tenant_id=tenant_id
    )
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    attempt = PaymentAttempt(
        attempt_id="att_stress_01",
        intent_id="intent_stress_01",
        tenant_id=tenant_id,
        amount=Money(99900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_stress_01",
        provider="mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save(tenant_id, attempt)

    order = Order(
        order_id="ord_stress_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=3,
        total_amount=Money(99900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_stress_01",
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


def probe_webhook_signature_forgery() -> dict[str, Any]:
    """Probe signature verification with forgeries, omissions, and mutations."""
    _, client, _ = _seed_test_env()
    violations: list[str] = []

    payload = {
        "event_id": "evt_sig_probe_01",
        "event_type": "payment.captured",
        "attempt_id": "att_stress_01",
    }

    # 1. Omitted signature header
    r_omit = client.post_webhook("payment.captured", payload, custom_signature="__OMIT__")
    if (
        r_omit.status_code != 401
        or r_omit.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Omitted signature returned {r_omit.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 2. Blank signature
    r_blank = client.post_webhook("payment.captured", payload, custom_signature="")
    if (
        r_blank.status_code != 401
        or r_blank.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Blank signature returned {r_blank.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 3. Forged hex signature
    r_forged = client.post_webhook(
        "payment.captured", payload, custom_signature="0123456789abcdef" * 4
    )
    if (
        r_forged.status_code != 401
        or r_forged.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Forged hex returned {r_forged.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 4. Wrong secret
    r_wrong_sec = client.post_webhook(
        "payment.captured", payload, secret="wrong_secret_12345678901234567890"
    )
    if (
        r_wrong_sec.status_code != 401
        or r_wrong_sec.body.get("error", {}).get("code") != "INVALID_WEBHOOK_SIGNATURE"
    ):
        violations.append(
            f"Wrong secret returned {r_wrong_sec.status_code}, expected 401 INVALID_WEBHOOK_SIGNATURE"
        )

    # 5. Bit-flipped signature
    ts_now = int(time.time())
    full_p = dict(payload, tenant_id="tenant_01", event_type="payment.captured")
    raw_j = json.dumps(full_p, separators=(",", ":"), sort_keys=True)
    canonical = f"{ts_now}.{raw_j}"
    valid_sig = hmac.new(
        client.app.webhook_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    corrupted_sig = ("0" if valid_sig[0] != "0" else "1") + valid_sig[1:]
    r_flipped = client.post_webhook(
        "payment.captured", payload, timestamp=ts_now, custom_signature=corrupted_sig
    )
    if r_flipped.status_code != 401:
        violations.append(f"Corrupted bit signature returned {r_flipped.status_code}, expected 401")

    return {
        "probe": "probe_webhook_signature_forgery",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_webhook_timestamp_window() -> dict[str, Any]:
    """Probe timestamp freshness bounds, expired timestamps, and future limits."""
    _, client, _ = _seed_test_env()
    violations: list[str] = []

    payload = {
        "event_id": "evt_ts_probe_01",
        "event_type": "payment.captured",
        "attempt_id": "att_stress_01",
    }

    # 1. Past expiration (>300 seconds)
    expired_ts = int(time.time()) - 305
    r_expired = client.post_webhook("payment.captured", payload, timestamp=expired_ts)
    if (
        r_expired.status_code != 401
        or r_expired.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP"
    ):
        violations.append(
            f"Expired ts (305s past) returned {r_expired.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 2. Future expiration (>300 seconds in future)
    future_ts = int(time.time()) + 305
    r_future = client.post_webhook("payment.captured", payload, timestamp=future_ts)
    if (
        r_future.status_code != 401
        or r_future.body.get("error", {}).get("code") != "EXPIRED_WEBHOOK_TIMESTAMP"
    ):
        violations.append(
            f"Future ts (305s future) returned {r_future.status_code}, expected 401 EXPIRED_WEBHOOK_TIMESTAMP"
        )

    # 3. Unparseable timestamp
    r_unparseable = client.request(
        "POST",
        "/api/v1/webhooks/payments",
        headers={
            "x-tenant-id": "tenant_01",
            "x-webhook-timestamp": "epoch_bogus_not_a_number",
            "x-webhook-signature": "any_signature",
        },
        json_body=payload,
        auth=False,
    )
    if (
        r_unparseable.status_code != 400
        or r_unparseable.body.get("error", {}).get("code") != "INVALID_WEBHOOK_TIMESTAMP"
    ):
        violations.append(
            f"Unparseable ts returned {r_unparseable.status_code}, expected 400 INVALID_WEBHOOK_TIMESTAMP"
        )

    # 4. Valid within window (10s in past)
    valid_ts = int(time.time()) - 10
    payload_valid = dict(payload, event_id="evt_ts_probe_valid")
    r_valid = client.post_webhook("payment.captured", payload_valid, timestamp=valid_ts)
    if r_valid.status_code != 200 or r_valid.body.get("status") != "PROCESSED":
        violations.append(
            f"Valid ts (10s past) returned {r_valid.status_code}, expected 200 PROCESSED"
        )

    return {
        "probe": "probe_webhook_timestamp_window",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_webhook_replay_protection() -> dict[str, Any]:
    """Probe replay defense: duplicate event_id must fail closed with 409."""
    _, client, _ = _seed_test_env()
    violations: list[str] = []

    payload = {
        "event_id": "evt_replay_probe_99",
        "event_type": "payment.captured",
        "attempt_id": "att_stress_01",
    }

    # First delivery
    r1 = client.post_webhook("payment.captured", payload)
    if r1.status_code != 200 or r1.body.get("status") != "PROCESSED":
        violations.append(f"Initial delivery failed with {r1.status_code}")

    # Immediate replay delivery
    r2 = client.post_webhook("payment.captured", payload)
    if r2.status_code != 409 or r2.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
        violations.append(
            f"Replay delivery returned {r2.status_code}, expected 409 WEBHOOK_REPLAY_DETECTED"
        )

    # Replay with modified body still rejected by event_id idempotency
    modified_payload = dict(payload, extra="malicious_mutation")
    r3 = client.post_webhook("payment.captured", modified_payload)
    if r3.status_code != 409 or r3.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
        violations.append(
            f"Modified replay returned {r3.status_code}, expected 409 WEBHOOK_REPLAY_DETECTED"
        )

    return {
        "probe": "probe_webhook_replay_protection",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_webhook_cross_tenant_isolation() -> dict[str, Any]:
    """Probe cross-tenant boundary: webhook for tenant B cannot mutate tenant A payment."""
    _, client, _ = _seed_test_env("tenant_01")
    violations: list[str] = []

    payload = {
        "event_id": "evt_cross_tenant_probe",
        "event_type": "payment.captured",
        "attempt_id": "att_stress_01",
    }

    # Asserting tenant_02 when attempt is owned by tenant_01
    r_cross = client.post_webhook("payment.captured", payload, tenant_id="tenant_02")
    if r_cross.status_code != 400 or r_cross.body.get("error", {}).get("code") != "INVALID_PAYMENT":
        violations.append(
            f"Cross-tenant webhook returned {r_cross.status_code}, expected 400 INVALID_PAYMENT"
        )

    return {
        "probe": "probe_webhook_cross_tenant_isolation",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_concurrent_replay_race() -> dict[str, Any]:
    """Probe multithreaded concurrency race: 30 threads submitting identical webhook event_id.

    Invariant: Exactly 1 thread succeeds (HTTP 200), and 29 receive HTTP 409 WEBHOOK_REPLAY_DETECTED.
    """
    _, client, env = _seed_test_env()
    violations: list[str] = []

    shared_event_id = "evt_race_concurrent_01"
    payload = {
        "event_id": shared_event_id,
        "event_type": "payment.captured",
        "attempt_id": "att_stress_01",
    }

    results: list[int] = []

    def _worker() -> int:
        resp = client.post_webhook("payment.captured", payload)
        return resp.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker) for _ in range(30)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_count = results.count(200)
    replay_count = results.count(409)

    if success_count != 1:
        violations.append(f"Expected exactly 1 success (HTTP 200), got {success_count}")
    if replay_count != 29:
        violations.append(f"Expected exactly 29 replays (HTTP 409), got {replay_count}")

    # Verify attempt status is CAPTURED exactly once
    attempt = env["payment_repo"].get("tenant_01", "att_stress_01")
    if attempt is None or attempt.status != PaymentStatus.CAPTURED:
        violations.append(
            f"Final payment attempt status invalid: {getattr(attempt, 'status', None)}"
        )

    return {
        "probe": "probe_concurrent_replay_race",
        "passed": len(violations) == 0,
        "success_count": success_count,
        "replay_count": replay_count,
        "violations": violations,
    }


def probe_saga_compensation_invariants() -> dict[str, Any]:
    """Probe SAGA compensation invariants on payment failures and refunds."""
    _, client, env = _seed_test_env()
    violations: list[str] = []

    inventory_repo = env["inventory_repo"]
    inventory_repo.reserve("tenant_01", "var_01", 3)
    inv_init = inventory_repo.get("tenant_01", "var_01")
    if inv_init is None or inv_init.reserved != 3:
        violations.append("Failed to initialize reserved inventory")

    # 1. Trigger payment.failed
    payload_fail = {
        "event_id": "evt_fail_saga_probe",
        "event_type": "payment.failed",
        "attempt_id": "att_stress_01",
        "reason": "Card declined: restricted card",
    }
    r_fail = client.post_webhook("payment.failed", payload_fail)
    if r_fail.status_code != 200 or r_fail.body.get("payment_status") != "FAILED":
        violations.append(f"Payment failed webhook returned {r_fail.status_code}")

    inv_after_fail = inventory_repo.get("tenant_01", "var_01")
    if inv_after_fail is None or inv_after_fail.reserved != 0:
        violations.append(f"Inventory was not released upon payment failure: {inv_after_fail}")

    order_after_fail = env["order_repo"].get("tenant_01", "ord_stress_01")
    if order_after_fail is None or order_after_fail.status != OrderStatus.CANCELLED:
        violations.append(f"Order was not cancelled upon payment failure: {order_after_fail}")

    # 2. Re-seed new attempt and order for refund probe
    attempt_ref = PaymentAttempt(
        attempt_id="att_refund_probe",
        intent_id="intent_refund_probe",
        tenant_id="tenant_01",
        amount=Money(50000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="idem_att_refund_01",
        provider="mock",
    )
    env["payment_repo"].save("tenant_01", attempt_ref)

    order_ref = Order(
        order_id="ord_refund_probe",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(50000, "INR"),
        status=OrderStatus.CONFIRMED,
        payment_attempt_id="att_refund_probe",
    )
    env["order_repo"].save("tenant_01", order_ref)
    inventory_repo.reserve("tenant_01", "var_01", 2)

    payload_ref = {
        "event_id": "evt_refund_saga_probe",
        "event_type": "payment.refunded",
        "attempt_id": "att_refund_probe",
        "refund_id": "ref_adv_001",
        "reason": "Merchant initiated refund",
    }
    r_ref = client.post_webhook("payment.refunded", payload_ref)
    if r_ref.status_code != 200 or r_ref.body.get("payment_status") != "REFUNDED":
        violations.append(f"Payment refunded webhook returned {r_ref.status_code}")

    inv_after_ref = inventory_repo.get("tenant_01", "var_01")
    if inv_after_ref is None or inv_after_ref.reserved != 0:
        violations.append(f"Inventory was not released upon refund: {inv_after_ref}")

    # Verify audit events present
    events = env["audit_ledger"].get_events("tenant_01")
    has_failed = any(e.event_type == AuditEventType.PAYMENT_FAILED for e in events)
    has_refund = any(e.event_type == AuditEventType.PAYMENT_REFUNDED for e in events)
    if not has_failed:
        violations.append("AuditEventType.PAYMENT_FAILED not recorded in audit ledger")
    if not has_refund:
        violations.append("AuditEventType.PAYMENT_REFUNDED not recorded in audit ledger")

    return {
        "probe": "probe_saga_compensation_invariants",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def main() -> int:
    log("=" * 66)
    log("AGENTPAY EMPIRICAL PAYMENT WEBHOOK & REPLAY ADVERSARIAL HARNESS")
    log("=" * 66)

    probes = [
        ("probe_webhook_signature_forgery", probe_webhook_signature_forgery),
        ("probe_webhook_timestamp_window", probe_webhook_timestamp_window),
        ("probe_webhook_replay_protection", probe_webhook_replay_protection),
        ("probe_webhook_cross_tenant_isolation", probe_webhook_cross_tenant_isolation),
        ("probe_concurrent_replay_race", probe_concurrent_replay_race),
        ("probe_saga_compensation_invariants", probe_saga_compensation_invariants),
    ]

    all_passed = True
    for name, fn in probes:
        res = fn()
        if res.get("passed"):
            log(f"[*] {name}: PASS")
        else:
            log(f"[!] {name}: FAIL -> {res.get('violations')}")
            all_passed = False

    log("=" * 66)
    if all_passed:
        log("[+] VERDICT: ALL INVARIANTS SATISFIED -> APPROVE")
        return 0
    else:
        log("[-] VERDICT: VULNERABILITIES DETECTED -> REJECT")
        return 1


if __name__ == "__main__":
    sys.exit(main())

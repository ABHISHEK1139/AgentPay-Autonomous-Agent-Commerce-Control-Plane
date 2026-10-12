"""Comprehensive test suite for Payment Webhook Ingestion, HMAC verification, replay defense, and reconciliation (§61)."""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Any

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.payment_repository import (
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
)
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.api.factory import create_app
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.audit import AuditEventType
from agentpay.domain.commerce import Inventory, ProductVariant
from agentpay.domain.errors import InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderTransactionMapping,
    ProviderWebhookEvent,
    WebhookEventStatus,
)


def _seed_test_env(
    tenant_id: str = "tenant_01",
) -> tuple[AgentPayApiApp, ApiTestClient, dict[str, Any]]:
    orch, _, _, _, _, _, _, inventory_repo, _, audit_ledger, _ = setup_orchestrator(
        tenant_id=tenant_id
    )
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    # Seed PaymentAttempt
    attempt = PaymentAttempt(
        attempt_id="att_test_01",
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
        order_id="ord_test_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(129900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_test_01",
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


def test_webhook_missing_signature_returns_401() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_001",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    # Pass custom_signature="__OMIT__" to omit the X-Webhook-Signature header
    resp = client.post_webhook("payment.captured", payload, custom_signature="__OMIT__")
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_webhook_blank_signature_returns_401() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_002",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    resp = client.post_webhook("payment.captured", payload, custom_signature="")
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_webhook_invalid_signature_returns_401() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_003",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    bogus_sig = "a" * 64
    resp = client.post_webhook("payment.captured", payload, custom_signature=bogus_sig)
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_webhook_mismatched_secret_returns_401() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_004",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    # Signed with wrong secret
    resp = client.post_webhook(
        "payment.captured",
        payload,
        secret="whsec_wrong_attacker_secret_value_32bytes",
    )
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_webhook_expired_timestamp_returns_401() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_005",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    # Timestamp > 300 seconds old
    expired_ts = int(time.time()) - 350
    resp = client.post_webhook("payment.captured", payload, timestamp=expired_ts)
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "EXPIRED_WEBHOOK_TIMESTAMP"


def test_webhook_unparseable_timestamp_returns_400() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_006",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    resp = client.request(
        "POST",
        "/api/v1/webhooks/payments",
        headers={
            "x-tenant-id": "tenant_01",
            "x-webhook-timestamp": "not_a_valid_timestamp",
            "x-webhook-signature": "some_sig",
        },
        json_body=payload,
        auth=False,
    )
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_TIMESTAMP"


def test_webhook_duplicate_replay_returns_409() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_replay_001",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
        "provider_payment_id": "prov_pay_999",
    }

    # First delivery succeeds
    resp1 = client.post_webhook("payment.captured", payload)
    assert resp1.status_code == 200
    assert resp1.body["status"] == "PROCESSED"

    # Second delivery with identical event_id is rejected as replay
    resp2 = client.post_webhook("payment.captured", payload)
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"


def test_webhook_payment_captured_lifecycle() -> None:
    _, client, env = _seed_test_env()
    payload = {
        "event_id": "evt_cap_001",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
        "provider_payment_id": "rzp_pay_captured_777",
    }

    resp = client.post_webhook("payment.captured", payload)
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "CAPTURED"
    assert resp.body["order_status"] == "CONFIRMED"

    # Verify payment attempt updated
    updated_attempt = env["payment_repo"].get("tenant_id", "att_test_01") or env[
        "payment_repo"
    ].get("tenant_01", "att_test_01")
    assert updated_attempt is not None
    assert updated_attempt.status == PaymentStatus.CAPTURED
    assert updated_attempt.provider_payment_id == "rzp_pay_captured_777"

    # Verify order confirmed
    updated_order = env["order_repo"].get("tenant_01", "ord_test_01")
    assert updated_order is not None
    assert updated_order.status == OrderStatus.CONFIRMED

    # Verify audit event
    events = env["audit_ledger"].get_events("tenant_01")
    captured_events = [e for e in events if e.event_type == AuditEventType.PAYMENT_CAPTURED]
    assert len(captured_events) >= 1
    assert captured_events[0].actor == "provider_webhook"


def test_webhook_payment_failed_cancels_order_and_releases_inventory() -> None:
    _, client, env = _seed_test_env()

    # Reserve inventory first to test SAGA compensation
    inventory_repo = env["inventory_repo"]
    inventory_repo.reserve("tenant_01", "var_01", 2)
    inv_before = inventory_repo.get("tenant_01", "var_01")
    assert inv_before is not None
    assert inv_before.reserved == 2
    assert inv_before.effective_available == 8

    payload = {
        "event_id": "evt_fail_001",
        "event_type": "payment.failed",
        "attempt_id": "att_test_01",
        "reason": "Card declined: insufficient funds",
    }

    resp = client.post_webhook("payment.failed", payload)
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "FAILED"
    assert resp.body["order_status"] == "CANCELLED"

    # Verify payment attempt failed
    attempt = env["payment_repo"].get("tenant_01", "att_test_01")
    assert attempt is not None
    assert attempt.status == PaymentStatus.FAILED
    assert attempt.failure_reason == "Card declined: insufficient funds"

    # Verify order cancelled
    order = env["order_repo"].get("tenant_01", "ord_test_01")
    assert order is not None
    assert order.status == OrderStatus.CANCELLED

    # Verify inventory released via SAGA compensation
    inv_after = inventory_repo.get("tenant_01", "var_01")
    assert inv_after is not None
    assert inv_after.reserved == 0
    assert inv_after.available == 10
    assert inv_after.effective_available == 10

    # Verify audit events
    events = env["audit_ledger"].get_events("tenant_01")
    failed_events = [e for e in events if e.event_type == AuditEventType.PAYMENT_FAILED]
    cancelled_events = [e for e in events if e.event_type == AuditEventType.ORDER_CANCELLED]
    assert len(failed_events) >= 1
    assert len(cancelled_events) >= 1


def test_webhook_payment_refunded_creates_refund_and_restocks() -> None:
    _, client, env = _seed_test_env()

    # Set initial order and attempt as captured / confirmed
    attempt = env["payment_repo"].get("tenant_01", "att_test_01")
    assert attempt is not None
    env["payment_repo"].save(
        "tenant_01",
        attempt.transition_to(PaymentStatus.CAPTURED, provider_payment_id="prov_cap_01"),
    )
    order = env["order_repo"].get("tenant_01", "ord_test_01")
    assert order is not None
    env["order_repo"].save("tenant_01", order.confirm("att_test_01"))

    # Reserve inventory so restock can release it
    inventory_repo = env["inventory_repo"]
    inventory_repo.reserve("tenant_01", "var_01", 2)
    inv_before = inventory_repo.get("tenant_01", "var_01")
    assert inv_before is not None
    assert inv_before.reserved == 2
    assert inv_before.effective_available == 8

    payload = {
        "event_id": "evt_ref_001",
        "event_type": "payment.refunded",
        "attempt_id": "att_test_01",
        "refund_id": "ref_webhook_123",
        "reason": "Customer requested full refund",
    }

    resp = client.post_webhook("payment.refunded", payload)
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "REFUNDED"
    assert resp.body["order_status"] == "REFUNDED"
    assert resp.body["refund_id"] == "ref_webhook_123"

    # Verify payment attempt refunded
    ref_attempt = env["payment_repo"].get("tenant_01", "att_test_01")
    assert ref_attempt is not None
    assert ref_attempt.status == PaymentStatus.REFUNDED

    # Verify order refunded
    ref_order = env["order_repo"].get("tenant_01", "ord_test_01")
    assert ref_order is not None
    assert ref_order.status == OrderStatus.REFUNDED

    # Verify inventory released / restocked
    inv_after = inventory_repo.get("tenant_01", "var_01")
    assert inv_after is not None
    assert inv_after.reserved == 0
    assert inv_after.effective_available == 10

    # Verify audit event recorded
    events = env["audit_ledger"].get_events("tenant_01")
    refund_events = [e for e in events if e.event_type == AuditEventType.PAYMENT_REFUNDED]
    assert len(refund_events) >= 1
    assert refund_events[0].actor == "provider_webhook"


def test_webhook_missing_event_id_or_type_returns_400() -> None:
    _, client, _ = _seed_test_env()

    # Missing event_id
    payload_no_id = {
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    resp1 = client.post_webhook("payment.captured", payload_no_id)
    assert resp1.status_code == 400
    assert resp1.body["error"]["code"] == "INVALID_PAYLOAD"

    # Missing event_type
    payload_no_type = {
        "event_id": "evt_notype",
        "attempt_id": "att_test_01",
    }
    resp2 = client.post_webhook("", payload_no_type)
    assert resp2.status_code == 400
    assert resp2.body["error"]["code"] == "INVALID_PAYLOAD"


def test_webhook_nonexistent_payment_attempt_returns_400() -> None:
    _, client, _ = _seed_test_env()
    payload = {
        "event_id": "evt_ghost_01",
        "event_type": "payment.captured",
        "attempt_id": "att_nonexistent_9999",
    }
    resp = client.post_webhook("payment.captured", payload)
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "INVALID_PAYMENT"


def test_webhook_cross_tenant_access_fails_closed() -> None:
    _, client, _ = _seed_test_env("tenant_01")
    # Payment attempt exists in tenant_01, but webhook sent for tenant_02
    payload = {
        "event_id": "evt_cross_01",
        "event_type": "payment.captured",
        "attempt_id": "att_test_01",
    }
    resp = client.post_webhook("payment.captured", payload, tenant_id="tenant_02")
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "INVALID_PAYMENT"


def test_direct_reconciler_webhook_transitions() -> None:
    """Direct unit test of PaymentReconciler.reconcile_webhook_event."""
    provider = MockPaymentProvider()
    payment_repo = InMemoryPaymentRepository()
    order_repo = InMemoryOrderRepository()
    reconciler = PaymentReconciler(
        payment_provider=provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
    )

    tenant_id = "tenant_test"
    attempt = PaymentAttempt(
        attempt_id="att_direct_01",
        intent_id="intent_direct_01",
        tenant_id=tenant_id,
        amount=Money(5000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_direct_01",
        provider="mock",
    )
    payment_repo.save(tenant_id, attempt)

    order = Order(
        order_id="ord_direct_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        buyer_id="buyer_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(5000, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_direct_01",
    )
    order_repo.save(tenant_id, order)

    # 1. Test missing attempt_id
    with pytest.raises(InvalidPaymentError, match="Missing attempt_id"):
        reconciler.reconcile_webhook_event(tenant_id, "payment.captured", {})

    # 2. Test unknown event type
    with pytest.raises(InvalidPaymentError, match="Unsupported webhook event type"):
        reconciler.reconcile_webhook_event(
            tenant_id,
            "payment.something_unknown",
            {"attempt_id": "att_direct_01"},
        )

    # 3. Test successful capture
    res = reconciler.reconcile_webhook_event(
        tenant_id,
        "payment.captured",
        {"attempt_id": "att_direct_01", "provider_payment_id": "prov_captured_111"},
    )
    assert res["status"] == "PROCESSED"
    assert res["payment_status"] == "CAPTURED"
    assert res["order_status"] == "CONFIRMED"

    saved_attempt = payment_repo.get(tenant_id, "att_direct_01")
    assert saved_attempt is not None
    assert saved_attempt.status == PaymentStatus.CAPTURED
    assert saved_attempt.provider_payment_id == "prov_captured_111"

    saved_order = order_repo.get(tenant_id, "ord_direct_01")
    assert saved_order is not None
    assert saved_order.status == OrderStatus.CONFIRMED


def test_webhook_concurrent_20_threads_failed_prevents_inventory_theft() -> None:
    """20 concurrent threads posting identical payment.failed events via threading.Barrier
    must result in 1x 200 PROCESSED, 19x 409 WEBHOOK_REPLAY_DETECTED, and single inventory release.
    """
    import concurrent.futures
    import sys
    import threading

    orch, _, _, _, _, _, var_repo, inv_repo, _, audit_ledger, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    var_repo.save(
        "tenant_01",
        ProductVariant("var_test", "prod_1", "SKU1", "Item 1", Money(1000, "INR")),
    )
    inv_repo.save("tenant_01", Inventory("var_test", "tenant_01", available=50, reserved=30))

    attempt = PaymentAttempt(
        "att_conc_fail",
        "int_conc_fail",
        "tenant_01",
        Money(1000, "INR"),
        PaymentStatus.CREATED,
        "idem_c_fail",
        "mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", attempt)
    order = Order(
        "ord_conc_fail",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_test",
        2,
        Money(1000, "INR"),
        OrderStatus.PENDING_PAYMENT,
        "att_conc_fail",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_race_fail_001",
        "event_type": "payment.failed",
        "attempt_id": "att_conc_fail",
    }
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    orig_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:

        def worker(_: int) -> int:
            barrier.wait()
            resp = client.post_webhook("payment.failed", payload)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            codes = list(executor.map(worker, range(num_threads)))
    finally:
        sys.setswitchinterval(orig_interval)

    assert codes.count(200) == 1
    assert codes.count(409) == 19
    assert set(codes) == {200, 409}

    inv = inv_repo.get("tenant_01", "var_test")
    assert inv is not None
    assert inv.reserved == 28

    events = audit_ledger.get_events("tenant_01")
    fail_events = [
        e
        for e in events
        if e.event_type == AuditEventType.PAYMENT_FAILED
        and e.data.get("attempt_id") == "att_conc_fail"
    ]
    cancel_events = [
        e
        for e in events
        if e.event_type == AuditEventType.ORDER_CANCELLED
        and e.data.get("order_id") == "ord_conc_fail"
    ]
    assert len(fail_events) == 1
    assert len(cancel_events) == 1


def test_webhook_concurrent_20_threads_captured_prevents_duplicate_capture() -> None:
    """20 concurrent threads posting identical payment.captured events must result in
    1x 200 PROCESSED and 19x 409 WEBHOOK_REPLAY_DETECTED.
    """
    import concurrent.futures
    import sys
    import threading

    orch, _, _, _, _, _, _, _, _, audit_ledger, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    attempt = PaymentAttempt(
        "att_conc_cap",
        "int_conc_cap",
        "tenant_01",
        Money(5000, "INR"),
        PaymentStatus.CREATED,
        "idem_c_cap",
        "mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", attempt)
    order = Order(
        "ord_conc_cap",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_01",
        1,
        Money(5000, "INR"),
        OrderStatus.PENDING_PAYMENT,
        "att_conc_cap",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_race_cap_001",
        "event_type": "payment.captured",
        "attempt_id": "att_conc_cap",
        "provider_payment_id": "rzp_race_cap_001",
    }
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    orig_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:

        def worker(_: int) -> int:
            barrier.wait()
            resp = client.post_webhook("payment.captured", payload)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            codes = list(executor.map(worker, range(num_threads)))
    finally:
        sys.setswitchinterval(orig_interval)

    assert codes.count(200) == 1
    assert codes.count(409) == 19
    assert set(codes) == {200, 409}

    saved_attempt = orch._payment_repo.get("tenant_01", "att_conc_cap")
    assert saved_attempt is not None and saved_attempt.status == PaymentStatus.CAPTURED
    saved_order = orch._order_repo.get("tenant_01", "ord_conc_cap")
    assert saved_order is not None and saved_order.status == OrderStatus.CONFIRMED

    events = audit_ledger.get_events("tenant_01")
    cap_events = [
        e
        for e in events
        if e.event_type == AuditEventType.PAYMENT_CAPTURED
        and e.data.get("attempt_id") == "att_conc_cap"
    ]
    assert len(cap_events) == 1


def test_webhook_concurrent_20_threads_refunded_prevents_double_restock() -> None:
    """20 concurrent threads posting identical payment.refunded events must result in
    1x 200 PROCESSED, 19x 409 WEBHOOK_REPLAY_DETECTED, and one inventory release.
    """
    import concurrent.futures
    import sys
    import threading

    orch, _, _, _, _, _, var_repo, inv_repo, _, _, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    var_repo.save(
        "tenant_01",
        ProductVariant("var_test_ref", "prod_1", "SKU1", "Item 1", Money(2000, "INR")),
    )
    inv_repo.save("tenant_01", Inventory("var_test_ref", "tenant_01", available=30, reserved=20))

    attempt = PaymentAttempt(
        "att_conc_ref",
        "int_conc_ref",
        "tenant_01",
        Money(2000, "INR"),
        PaymentStatus.CAPTURED,
        "idem_c_ref",
        "mock",
        provider_payment_id="prov_c_ref",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", attempt)
    order = Order(
        "ord_conc_ref",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_test_ref",
        2,
        Money(2000, "INR"),
        OrderStatus.CONFIRMED,
        "att_conc_ref",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_race_ref_001",
        "event_type": "payment.refunded",
        "attempt_id": "att_conc_ref",
        "refund_id": "ref_race_conc_01",
    }
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    orig_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:

        def worker(_: int) -> int:
            barrier.wait()
            resp = client.post_webhook("payment.refunded", payload)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            codes = list(executor.map(worker, range(num_threads)))
    finally:
        sys.setswitchinterval(orig_interval)

    assert codes.count(200) == 1
    assert codes.count(409) == 19
    assert set(codes) == {200, 409}

    inv = inv_repo.get("tenant_01", "var_test_ref")
    assert inv is not None
    assert inv.reserved == 18


def test_webhook_successive_refunds_distinct_event_ids_preserve_inventory() -> None:
    """Successive delivery of payment.refunded with distinct event IDs must not over-release inventory."""
    orch, _, _, _, _, _, var_repo, inv_repo, _, _, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    var_repo.save(
        "tenant_01",
        ProductVariant("var_test_seq", "prod_1", "SKU1", "Item 1", Money(1000, "INR")),
    )
    inv_repo.save("tenant_01", Inventory("var_test_seq", "tenant_01", available=20, reserved=10))

    att = PaymentAttempt(
        "att_seq_02",
        "int_seq_02",
        "tenant_01",
        Money(1000, "INR"),
        PaymentStatus.CAPTURED,
        "idem_seq_02",
        "mock",
        provider_payment_id="prov_seq_02",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", att)
    ord_ = Order(
        "ord_seq_02",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_test_seq",
        2,
        Money(1000, "INR"),
        OrderStatus.CONFIRMED,
        "att_seq_02",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", ord_)

    r1 = client.post_webhook(
        "payment.refunded", {"event_id": "e_refund_1", "attempt_id": "att_seq_02"}
    )
    assert r1.status_code == 200
    assert r1.body["status"] == "PROCESSED"
    assert r1.body["payment_status"] == "REFUNDED"

    inv_after_1 = inv_repo.get("tenant_01", "var_test_seq")
    assert inv_after_1 is not None and inv_after_1.reserved == 8

    r2 = client.post_webhook(
        "payment.refunded", {"event_id": "e_refund_2", "attempt_id": "att_seq_02"}
    )
    assert r2.status_code == 200
    assert r2.body["status"] == "PROCESSED"

    inv_after_2 = inv_repo.get("tenant_01", "var_test_seq")
    assert inv_after_2 is not None
    assert inv_after_2.reserved == 8


def test_webhook_successive_failed_distinct_event_ids_preserve_inventory() -> None:
    """Successive delivery of payment.failed with distinct event IDs must not repeatedly release inventory."""
    orch, _, _, _, _, _, var_repo, inv_repo, _, _, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    var_repo.save(
        "tenant_01",
        ProductVariant("var_test_seq_f", "prod_1", "SKU1", "Item 1", Money(1000, "INR")),
    )
    inv_repo.save("tenant_01", Inventory("var_test_seq_f", "tenant_01", available=20, reserved=10))

    att = PaymentAttempt(
        "att_seq_f",
        "int_seq_f",
        "tenant_01",
        Money(1000, "INR"),
        PaymentStatus.CREATED,
        "idem_seq_f",
        "mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", att)
    ord_ = Order(
        "ord_seq_f",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_test_seq_f",
        2,
        Money(1000, "INR"),
        OrderStatus.PENDING_PAYMENT,
        "att_seq_f",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", ord_)

    r1 = client.post_webhook("payment.failed", {"event_id": "e_fail_1", "attempt_id": "att_seq_f"})
    assert r1.status_code == 200
    assert r1.body["payment_status"] == "FAILED"
    assert r1.body["order_status"] == "CANCELLED"

    inv_after_1 = inv_repo.get("tenant_01", "var_test_seq_f")
    assert inv_after_1 is not None and inv_after_1.reserved == 8

    r2 = client.post_webhook("payment.failed", {"event_id": "e_fail_2", "attempt_id": "att_seq_f"})
    assert r2.status_code == 200

    inv_after_2 = inv_repo.get("tenant_01", "var_test_seq_f")
    assert inv_after_2 is not None
    assert inv_after_2.reserved == 8


def test_webhook_refund_rejected_on_non_captured_payment() -> None:
    """Refunding a payment attempt that is NOT in CAPTURED status must fail closed and not touch inventory."""
    orch, _, _, _, _, _, var_repo, inv_repo, _, _, _ = setup_orchestrator("tenant_01")
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    var_repo.save(
        "tenant_01",
        ProductVariant("var_test_nocap", "prod_1", "SKU1", "Item 1", Money(1000, "INR")),
    )
    inv_repo.save("tenant_01", Inventory("var_test_nocap", "tenant_01", available=20, reserved=10))

    att = PaymentAttempt(
        "att_failed_nocap",
        "int_failed_nocap",
        "tenant_01",
        Money(1000, "INR"),
        PaymentStatus.FAILED,
        "idem_fnocap",
        "mock",
        failure_reason="Card declined",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", att)
    ord_ = Order(
        "ord_failed_nocap",
        "tenant_01",
        "merch_01",
        "user_01",
        "agent_01",
        "var_test_nocap",
        2,
        Money(1000, "INR"),
        OrderStatus.CANCELLED,
        "att_failed_nocap",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", ord_)

    resp = client.post_webhook(
        "payment.refunded", {"event_id": "e_ill_ref", "attempt_id": "att_failed_nocap"}
    )
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "INVALID_PAYMENT"

    inv = inv_repo.get("tenant_01", "var_test_nocap")
    assert inv is not None
    assert inv.reserved == 10

    saved_attempt = orch._payment_repo.get("tenant_01", "att_failed_nocap")
    assert saved_attempt is not None and saved_attempt.status == PaymentStatus.FAILED


def test_direct_reconciler_state_machine_guards_and_idempotency() -> None:
    """Direct unit tests for PaymentReconciler state machine validation and idempotent returns."""
    provider = MockPaymentProvider()
    payment_repo = InMemoryPaymentRepository()
    order_repo = InMemoryOrderRepository()
    reconciler = PaymentReconciler(
        payment_provider=provider,
        payment_repo=payment_repo,
        order_repo=order_repo,
    )
    tenant_id = "tenant_reconciler_test"

    att_created = PaymentAttempt(
        "att_cr", "int_cr", tenant_id, Money(1000, "INR"), PaymentStatus.CREATED, "idem_cr", "mock"
    )
    payment_repo.save(tenant_id, att_created)
    with pytest.raises(InvalidPaymentError, match="Cannot refund payment"):
        reconciler.reconcile_webhook_event(tenant_id, "payment.refunded", {"attempt_id": "att_cr"})

    att_failed = PaymentAttempt(
        "att_fl", "int_fl", tenant_id, Money(1000, "INR"), PaymentStatus.FAILED, "idem_fl", "mock"
    )
    payment_repo.save(tenant_id, att_failed)
    with pytest.raises(InvalidPaymentError, match="Cannot refund payment"):
        reconciler.reconcile_webhook_event(tenant_id, "payment.refunded", {"attempt_id": "att_fl"})

    res_fail = reconciler.reconcile_webhook_event(
        tenant_id, "payment.failed", {"attempt_id": "att_fl"}
    )
    assert res_fail["status"] == "PROCESSED"
    assert res_fail["payment_status"] == "FAILED"

    with pytest.raises(InvalidPaymentError, match="Cannot capture payment"):
        reconciler.reconcile_webhook_event(tenant_id, "payment.captured", {"attempt_id": "att_fl"})

    att_refunded = PaymentAttempt(
        "att_rf",
        "int_rf",
        tenant_id,
        Money(1000, "INR"),
        PaymentStatus.REFUNDED,
        "idem_rf",
        "mock",
    )
    payment_repo.save(tenant_id, att_refunded)
    res_ref = reconciler.reconcile_webhook_event(
        tenant_id, "payment.refunded", {"attempt_id": "att_rf"}
    )
    assert res_ref["status"] == "PROCESSED"
    assert res_ref["payment_status"] == "REFUNDED"


def test_b_wh_01_raw_bytes_hmac_verification() -> None:
    """B-WH-01: Real PSP raw request bytes verify successfully without re-serialization drift."""
    import hashlib
    import hmac

    app, client, _ = _seed_test_env("tenant_01")
    raw_payload_bytes = (
        b'{"zebra": 100, "apple": 200, "event_id": "evt_psp_raw_01", '
        b'"attempt_id": "att_test_01", "event_type": "payment.captured", "tenant_id": "tenant_01"}'
    )
    secret = app.webhook_secret
    psp_sig = hmac.new(secret.encode("utf-8"), raw_payload_bytes, hashlib.sha256).hexdigest()

    resp = client.post_webhook(
        "payment.captured",
        payload={
            "zebra": 100,
            "apple": 200,
            "event_id": "evt_psp_raw_01",
            "attempt_id": "att_test_01",
        },
        tenant_id="tenant_01",
        custom_signature=psp_sig,
        raw_body=raw_payload_bytes,
    )
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"


def test_b_wh_02_future_timestamp_skew_rejection() -> None:
    """B-WH-02: Future timestamp skew > 30s is rejected; <= 30s is tolerated."""
    _, client, _ = _seed_test_env("tenant_01")
    now_epoch = int(time.time())

    # Skew > 30s in future must be rejected
    for future_skew in [35, 60, 120, 290]:
        resp_future = client.post_webhook(
            "payment.captured",
            payload={"event_id": f"evt_fut_{future_skew}", "attempt_id": "att_test_01"},
            tenant_id="tenant_01",
            timestamp=now_epoch + future_skew,
        )
        assert resp_future.status_code == 401
        assert resp_future.body["error"]["code"] == "EXPIRED_WEBHOOK_TIMESTAMP"

    # Small clock skew (e.g. +10s) is accepted
    resp_tolerated = client.post_webhook(
        "payment.captured",
        payload={"event_id": "evt_fut_tolerated_10", "attempt_id": "att_test_01"},
        tenant_id="tenant_01",
        timestamp=now_epoch + 10,
    )
    assert resp_tolerated.status_code == 200


def test_b_wh_03_missing_tenant_rejected() -> None:
    """B-WH-03: Webhook payload without identifiable tenant or mapped transaction is quarantined with 422 UNRESOLVED_WEBHOOK_TRANSACTION (§61, AP-001)."""
    _, client, _ = _seed_test_env("tenant_01")
    resp = client.post_webhook(
        "payment.captured",
        payload={"event_id": "evt_no_tenant_01", "attempt_id": "att_test_unmapped"},
        tenant_id="",  # empty tenant
    )
    assert resp.status_code == 422
    assert resp.body["error"]["code"] == "UNRESOLVED_WEBHOOK_TRANSACTION"


def test_b_wh_04_cross_tenant_event_replay_defense() -> None:
    """B-WH-04: Same provider event_id submitted to different tenants is rejected as replay."""
    app, client, _ = _seed_test_env("tenant_01")
    # Seed attempt in tenant_02 as well
    attempt_02 = PaymentAttempt(
        attempt_id="att_test_02",
        intent_id="intent_test_02",
        tenant_id="tenant_02",
        amount=Money(5000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_02",
        provider="mock",
    )
    assert app.orchestrator._payment_repo is not None
    app.orchestrator._payment_repo.save("tenant_02", attempt_02)

    # First delivery to tenant_01 succeeds
    resp1 = client.post_webhook(
        "payment.captured",
        payload={"event_id": "evt_global_unique_01", "attempt_id": "att_test_01"},
        tenant_id="tenant_01",
    )
    assert resp1.status_code == 200

    # Second delivery with SAME event_id to tenant_02 is rejected with 409
    resp2 = client.post_webhook(
        "payment.captured",
        payload={"event_id": "evt_global_unique_01", "attempt_id": "att_test_02"},
        tenant_id="tenant_02",
    )
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"


def test_b_code_01_type_error_on_invalid_role() -> None:
    """B-CODE-01: Passing invalid role raises explicit TypeError instead of assert."""
    _, client, _ = _seed_test_env("tenant_01")
    with pytest.raises(TypeError, match="Expected UserRole or None"):
        client.get_or_create_token("tenant_01", role="not_a_role")


def test_b_sbom_01_api_endpoint() -> None:
    """B-SBOM-01: GET /api/v1/security/sbom returns valid CycloneDX 1.5 and SLSA 1.2 provenance."""
    _, client, _ = _seed_test_env("tenant_01")
    resp = client.get("/api/v1/security/sbom")
    assert resp.status_code == 200
    assert resp.body["format"] == "CycloneDX-1.5"
    assert resp.body["spec_version"] == "1.5"
    assert resp.body["component_count"] > 0
    assert "sbom" in resp.body
    assert "provenance" in resp.body
    assert resp.body["provenance"]["_type"] == "https://in-toto.io/Statement/v1"


def test_b_rec_01_webhook_reconciliation_resolves_order_via_find_by_payment_attempt() -> None:
    """B-REC-01 Regression Test: Webhook reconciliation resolves Order via find_by_payment_attempt when payload lacks order_id (§41, §61)."""
    app, client, _ = _seed_test_env("tenant_01")
    orch = app.orchestrator

    attempt = PaymentAttempt(
        attempt_id="att_rec_find_01",
        intent_id="intent_custom_rec",
        tenant_id="tenant_01",
        amount=Money(50000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_rec_find_01",
        provider="mock",
    )
    assert orch._payment_repo is not None
    orch._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_rec_find_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(50000, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_rec_find_01",
    )
    assert orch._order_repo is not None
    orch._order_repo.save("tenant_01", order)

    # Deliver webhook notification without order_id in the payload
    resp = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_rec_find_att_01",
            "attempt_id": "att_rec_find_01",
            "provider_payment_id": "prov_rec_01",
        },
        tenant_id="tenant_01",
    )
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "CAPTURED"
    assert resp.body["order_id"] == "ord_rec_find_01"
    assert resp.body["order_status"] == "CONFIRMED"

    # Verify Order was updated to CONFIRMED
    updated_order = orch._order_repo.get("tenant_01", "ord_rec_find_01")
    assert updated_order is not None
    assert updated_order.status == OrderStatus.CONFIRMED


def test_ap001_mapping_resolution_without_tenant_header() -> None:
    """AP-001: Webhook without tenant header successfully resolves tenant strictly from ProviderTransactionMapping (§61, AP-001)."""
    app, client, _ = _seed_test_env("tenant_01")

    # Seed mapping in provider_mapping_repo
    mapping = ProviderTransactionMapping(
        mapping_id="map_ap001_01",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="mock",
        provider_payment_id="prov_mapped_01",
    )
    app.provider_mapping_repo.save(mapping)

    resp = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_ap001_res_01",
            "provider_payment_id": "prov_mapped_01",
        },
        tenant_id="",  # Explicitly omitted tenant
    )
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["tenant_id"] == "tenant_01"
    assert resp.body["payment_status"] == "CAPTURED"


def test_ap001_cross_tenant_assertion_fails_closed() -> None:
    """AP-001: Asserted tenant that conflicts with mapped tenant fails closed (§61, AP-001)."""
    app, client, _ = _seed_test_env("tenant_01")

    mapping = ProviderTransactionMapping(
        mapping_id="map_ap001_02",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="mock",
        provider_payment_id="prov_mapped_02",
    )
    app.provider_mapping_repo.save(mapping)

    # Caller asserts tenant_02 for mapping belonging to tenant_01
    resp = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_ap001_conflict_01",
            "provider_payment_id": "prov_mapped_02",
        },
        tenant_id="tenant_02",
    )
    assert resp.status_code == 400
    assert "CROSS_TENANT" in resp.body["error"]["code"] or "CrossTenantAccessError" in str(
        resp.body
    )


def test_ap001_unmapped_event_quarantined_returns_422() -> None:
    """AP-001: Unmapped webhook transaction is quarantined with 422 UNRESOLVED_WEBHOOK_TRANSACTION and audits to SYSTEM (§61, AP-001)."""
    _, client, env = _seed_test_env("tenant_01")

    resp = client.post_webhook(
        "payment.captured",
        payload={
            "event_id": "evt_ap001_unmapped_01",
            "provider_payment_id": "prov_never_mapped_999",
        },
        tenant_id="",  # No tenant
    )
    assert resp.status_code == 422
    assert resp.body["error"]["code"] == "UNRESOLVED_WEBHOOK_TRANSACTION"
    assert resp.body["error"]["details"]["status"] == "QUARANTINED"

    # Verify audit event in SYSTEM tenant
    audit_events = env["audit_ledger"].get_events("SYSTEM")
    sec_events = [e for e in audit_events if e.event_type == AuditEventType.SECURITY_ALERT]
    assert len(sec_events) >= 1
    assert sec_events[-1].data["reason"] == "UNRESOLVED_WEBHOOK_TRANSACTION"


def test_ap002_razorpay_webhook_valid_signature_success() -> None:
    """AP-002: Dedicated POST /api/v1/webhooks/razorpay validates raw body HMAC and reconciles order (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")

    mapping = ProviderTransactionMapping(
        mapping_id="map_rzp_01",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="razorpay",
        provider_payment_id="pay_rzp_valid_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_valid_01",
                    "amount": 129900,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
    resp = client.post_razorpay_webhook(payload)
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "CAPTURED"
    assert resp.body["order_status"] == "CONFIRMED"


def test_ap002_razorpay_webhook_invalid_signature_returns_401() -> None:
    """AP-002: Razorpay webhook with corrupted signature returns 401 INVALID_WEBHOOK_SIGNATURE (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"event": "payment.captured", "id": "evt_rzp_01"}
    resp = client.post_razorpay_webhook(payload, custom_signature="deadbeef" * 8)
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_razorpay_webhook_missing_signature_returns_401() -> None:
    """AP-002: Razorpay webhook with missing signature header returns 401 INVALID_WEBHOOK_SIGNATURE (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"event": "payment.captured", "id": "evt_rzp_02"}
    resp = client.post_razorpay_webhook(payload, custom_signature="__OMIT__")
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_razorpay_webhook_unmapped_quarantine_returns_422() -> None:
    """AP-002: Unmapped Razorpay transaction returns 422 UNRESOLVED_WEBHOOK_TRANSACTION (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_unmapped_404",
                    "amount": 50000,
                    "status": "captured",
                }
            }
        },
    }
    resp = client.post_razorpay_webhook(payload)
    assert resp.status_code == 422
    assert resp.body["error"]["code"] == "UNRESOLVED_WEBHOOK_TRANSACTION"


def test_ap002_razorpay_webhook_replay_defense_returns_409() -> None:
    """AP-002: Duplicate Razorpay webhook event is rejected with 409 WEBHOOK_REPLAY_DETECTED (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")
    mapping = ProviderTransactionMapping(
        mapping_id="map_rzp_replay",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="razorpay",
        provider_payment_id="pay_rzp_rep_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "event_id": "evt_rzp_unique_rep_01",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_rep_01",
                    "amount": 129900,
                    "status": "captured",
                }
            }
        },
    }
    resp1 = client.post_razorpay_webhook(payload)
    assert resp1.status_code == 200

    resp2 = client.post_razorpay_webhook(payload)
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"


def test_ap002_razorpay_cross_endpoint_rejection() -> None:
    """AP-002: Passing Stripe signature header to Razorpay endpoint is rejected with 401 (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"event": "payment.captured", "id": "evt_rzp_cross"}
    # Supply Stripe-Signature instead of X-Razorpay-Signature
    resp = client.request(
        "POST",
        "/api/v1/webhooks/razorpay",
        headers={"stripe-signature": "t=12345,v1=abcde"},
        json_body=payload,
        auth=False,
    )
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_razorpay_secret_rotation() -> None:
    """AP-002: Razorpay webhook verifies against rotated candidate secrets (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")
    app.razorpay_webhook_secret = "whsec_old_key_1,whsec_new_key_2"

    mapping = ProviderTransactionMapping(
        mapping_id="map_rzp_rot",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="razorpay",
        provider_payment_id="pay_rzp_rot_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "event_id": "evt_rzp_rot_01",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_rot_01",
                    "amount": 129900,
                    "status": "captured",
                }
            }
        },
    }
    # Test valid with old key
    resp_old = client.post_razorpay_webhook(payload, secret="whsec_old_key_1")
    assert resp_old.status_code == 200

    # Test valid with new key (different event_id)
    payload["event_id"] = "evt_rzp_rot_02"
    resp_new = client.post_razorpay_webhook(payload, secret="whsec_new_key_2")
    assert resp_new.status_code == 200

    # Test invalid with wrong key
    payload["event_id"] = "evt_rzp_rot_03"
    resp_bad = client.post_razorpay_webhook(payload, secret="whsec_wrong_key")
    assert resp_bad.status_code == 401


def test_ap002_stripe_webhook_valid_signature_success() -> None:
    """AP-002: Dedicated POST /api/v1/webhooks/stripe validates t=...,v1=... HMAC and reconciles order (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")

    mapping = ProviderTransactionMapping(
        mapping_id="map_stripe_01",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="stripe",
        provider_payment_id="pi_stripe_valid_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "id": "evt_stripe_valid_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_valid_01",
                "amount": 129900,
                "currency": "INR",
                "status": "succeeded",
            }
        },
    }
    resp = client.post_stripe_webhook(payload)
    assert resp.status_code == 200
    assert resp.body["status"] == "PROCESSED"
    assert resp.body["payment_status"] == "CAPTURED"
    assert resp.body["order_status"] == "CONFIRMED"


def test_ap002_stripe_webhook_invalid_signature_returns_401() -> None:
    """AP-002: Stripe webhook with corrupted signature returns 401 INVALID_WEBHOOK_SIGNATURE (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"id": "evt_stripe_01", "type": "payment_intent.succeeded"}
    resp = client.post_stripe_webhook(
        payload, custom_signature=f"t={int(time.time())},v1={'a' * 64}"
    )
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_stripe_webhook_missing_signature_returns_401() -> None:
    """AP-002: Stripe webhook with missing signature header returns 401 INVALID_WEBHOOK_SIGNATURE (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"id": "evt_stripe_02", "type": "payment_intent.succeeded"}
    resp = client.post_stripe_webhook(payload, custom_signature="__OMIT__")
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_stripe_webhook_expired_timestamp_returns_401() -> None:
    """AP-002: Stripe webhook with timestamp older than 300s returns 401 EXPIRED_WEBHOOK_TIMESTAMP (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"id": "evt_stripe_03", "type": "payment_intent.succeeded"}
    expired_ts = int(time.time()) - 350
    resp = client.post_stripe_webhook(payload, timestamp=expired_ts)
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "EXPIRED_WEBHOOK_TIMESTAMP"


def test_ap002_stripe_webhook_unmapped_quarantine_returns_422() -> None:
    """AP-002: Unmapped Stripe transaction returns 422 UNRESOLVED_WEBHOOK_TRANSACTION (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {
        "id": "evt_stripe_unmapped_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_unmapped_999",
                "amount": 50000,
                "status": "succeeded",
            }
        },
    }
    resp = client.post_stripe_webhook(payload)
    assert resp.status_code == 422
    assert resp.body["error"]["code"] == "UNRESOLVED_WEBHOOK_TRANSACTION"


def test_ap002_stripe_webhook_replay_defense_returns_409() -> None:
    """AP-002: Duplicate Stripe webhook event is rejected with 409 WEBHOOK_REPLAY_DETECTED (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")
    mapping = ProviderTransactionMapping(
        mapping_id="map_stripe_replay",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="stripe",
        provider_payment_id="pi_stripe_rep_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "id": "evt_stripe_unique_rep_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_rep_01",
                "amount": 129900,
                "status": "succeeded",
            }
        },
    }
    resp1 = client.post_stripe_webhook(payload)
    assert resp1.status_code == 200

    resp2 = client.post_stripe_webhook(payload)
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"


def test_ap002_stripe_cross_endpoint_rejection() -> None:
    """AP-002: Passing Razorpay signature header to Stripe endpoint is rejected with 401 (§61, AP-002)."""
    _, client, _ = _seed_test_env("tenant_01")
    payload = {"id": "evt_stripe_cross", "type": "payment_intent.succeeded"}
    # Supply X-Razorpay-Signature instead of Stripe-Signature
    resp = client.request(
        "POST",
        "/api/v1/webhooks/stripe",
        headers={"x-razorpay-signature": "deadbeef" * 8},
        json_body=payload,
        auth=False,
    )
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


def test_ap002_stripe_secret_rotation() -> None:
    """AP-002: Stripe webhook verifies against rotated candidate secrets (§61, AP-002)."""
    app, client, _ = _seed_test_env("tenant_01")
    app.stripe_webhook_secret = "whsec_old_key_1,whsec_new_key_2"

    mapping = ProviderTransactionMapping(
        mapping_id="map_stripe_rot",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="stripe",
        provider_payment_id="pi_stripe_rot_01",
    )
    app.provider_mapping_repo.save(mapping)

    payload = {
        "id": "evt_stripe_rot_01",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_rot_01",
                "amount": 129900,
                "status": "succeeded",
            }
        },
    }
    # Test valid with old key
    resp_old = client.post_stripe_webhook(payload, secret="whsec_old_key_1")
    assert resp_old.status_code == 200

    # Test valid with new key (different event_id)
    payload["id"] = "evt_stripe_rot_02"
    resp_new = client.post_stripe_webhook(payload, secret="whsec_new_key_2")
    assert resp_new.status_code == 200

    # Test invalid with wrong key
    payload["id"] = "evt_stripe_rot_03"
    resp_bad = client.post_stripe_webhook(payload, secret="whsec_wrong_key")
    assert resp_bad.status_code == 401


def test_ap003_domain_webhook_event_validation_and_transitions() -> None:
    """Validate domain invariants for ProviderWebhookEvent (§61, AP-003)."""
    # 1. Invalid empty event_id
    with pytest.raises(InvalidPaymentError, match="event_id must be a non-empty string"):
        ProviderWebhookEvent(
            id="pwe_test_01",
            provider="stripe",
            event_id="   ",
            event_type="payment.captured",
        )

    # 2. Invalid empty provider
    with pytest.raises(InvalidPaymentError, match="provider must be a non-empty string"):
        ProviderWebhookEvent(
            id="pwe_test_02",
            provider="",
            event_id="evt_valid_01",
            event_type="payment.captured",
        )

    # 3. Invalid empty id
    with pytest.raises(InvalidPaymentError, match="id must be a non-empty string"):
        ProviderWebhookEvent(
            id="",
            provider="stripe",
            event_id="evt_valid_01",
            event_type="payment.captured",
        )

    # 4. Valid creation and transitions: RECEIVED -> PROCESSING -> PROCESSED
    evt = ProviderWebhookEvent(
        id="pwe_test_03",
        provider="stripe",
        event_id="evt_valid_01",
        event_type="payment.captured",
        provider_account_id="acct_123",
    )
    assert evt.status == WebhookEventStatus.RECEIVED
    assert evt.attempt_count == 1

    evt_proc = evt.mark_processing()
    assert evt_proc.status == WebhookEventStatus.PROCESSING
    assert evt_proc.attempt_count == 2

    evt_done = evt_proc.mark_processed(entity_reference="att_01")
    assert evt_done.status == WebhookEventStatus.PROCESSED
    assert evt_done.entity_reference == "att_01"
    assert evt_done.last_error_code is None

    # 5. Invalid transition from PROCESSED
    with pytest.raises(InvalidPaymentError, match="Cannot transition webhook event from PROCESSED"):
        evt_done.mark_processing()

    with pytest.raises(InvalidPaymentError, match="Cannot transition webhook event from PROCESSED"):
        evt_done.mark_failed("cannot fail after processed")

    # 6. Failed and Quarantined paths
    evt_fail = ProviderWebhookEvent(
        id="pwe_test_04",
        provider="razorpay",
        event_id="evt_fail_01",
        event_type="payment.failed",
    )
    evt_fail_proc = evt_fail.mark_processing()
    evt_failed = evt_fail_proc.mark_failed("GATEWAY_ERROR")
    assert evt_failed.status == WebhookEventStatus.FAILED
    assert evt_failed.last_error_code == "GATEWAY_ERROR"

    evt_quar = ProviderWebhookEvent(
        id="pwe_test_05",
        provider="stripe",
        event_id="evt_quar_01",
        event_type="unknown.event",
    )
    evt_quarantined = evt_quar.mark_quarantined("UNKNOWN_EVENT")
    assert evt_quarantined.status == WebhookEventStatus.QUARANTINED
    assert evt_quarantined.last_error_code == "UNKNOWN_EVENT"


def test_ap003_durable_deduplication_restart_resilience_sqlite(tmp_path: Path) -> None:
    """Verify restart resilience across separate application instances using SQLite (§61, AP-003).

    Instance 1 processes the event. Instance 2 starts with an empty L1 in-memory cache,
    reads from the persistent SQLite database, and detects/blocks the replay with 409.
    """
    db_file = tmp_path / "webhook_restart_resilience.db"
    db_url = f"sqlite:///{db_file}"

    # 1. Instance 1 setup
    app1 = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
    client1 = ApiTestClient(app1)

    # Seed PaymentAttempt and Order in app1
    attempt = PaymentAttempt(
        attempt_id="att_restart_01",
        intent_id="intent_restart_01",
        tenant_id="tenant_01",
        amount=Money(129900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_restart_01",
        provider="mock",
    )
    assert app1.orchestrator._payment_repo is not None
    app1.orchestrator._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_restart_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(129900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_restart_01",
    )
    assert app1.orchestrator._order_repo is not None
    app1.orchestrator._order_repo.save("tenant_01", order)

    # Post first webhook to app1
    payload = {
        "event_id": "evt_durable_restart_01",
        "event_type": "payment.captured",
        "attempt_id": "att_restart_01",
        "provider": "mock",
        "amount": {"minor_units": 129900, "currency": "INR"},
    }
    resp1 = client1.post_webhook("payment.captured", payload)
    assert resp1.status_code == 200
    assert resp1.body.get("payment_status") == "CAPTURED"

    # Verify attempt in app1 is CAPTURED
    att_db = app1.orchestrator._payment_repo.get("tenant_01", "att_restart_01")
    assert att_db is not None
    assert att_db.status == PaymentStatus.CAPTURED

    # 2. Simulate server restart: spin up completely separate Instance 2 on same DB
    app2 = create_app(database_url=db_url, seed_demo_data=False, tenant_id="tenant_01")
    client2 = ApiTestClient(app2)

    # Verify App 2's L1 memory cache is completely empty!
    assert len(app2._processed_webhook_events) == 0

    # Post identical webhook event to App 2
    resp2 = client2.post_webhook("payment.captured", payload)

    # Must return 409 Conflict with WEBHOOK_REPLAY_DETECTED
    assert resp2.status_code == 409
    assert resp2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"
    assert "already been processed" in resp2.body["error"]["message"]

    # Verify App 2's L1 cache was populated from DB
    assert "mock::evt_durable_restart_01" in app2._processed_webhook_events

    # Verify durable record exists in repo
    assert app2.webhook_event_repo is not None
    stored_event = app2.webhook_event_repo.find_by_provider_event("mock", "evt_durable_restart_01")
    assert stored_event is not None
    assert stored_event.status == WebhookEventStatus.PROCESSED
    assert stored_event.entity_reference == "att_restart_01"


def test_ap003_durable_deduplication_concurrent_race_sqlite(tmp_path: Path) -> None:
    """Stress test multithreaded concurrent deliveries of same webhook against SQLite (§61, AP-003).

    10 concurrent threads deliver the exact same event. Exactly 1 must receive 200 PROCESSED,
    and 9 must receive 409 WEBHOOK_REPLAY_DETECTED.
    """
    db_file = tmp_path / "webhook_concurrency_race.db"
    db_url = f"sqlite:///{db_file}"

    app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")

    attempt = PaymentAttempt(
        attempt_id="att_race_sql_01",
        intent_id="intent_race_sql_01",
        tenant_id="tenant_01",
        amount=Money(129900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_race_sql_01",
        provider="mock",
    )
    assert app.orchestrator._payment_repo is not None
    app.orchestrator._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_race_sql_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(129900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_race_sql_01",
    )
    assert app.orchestrator._order_repo is not None
    app.orchestrator._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_concurrent_race_sql_999",
        "event_type": "payment.captured",
        "attempt_id": "att_race_sql_01",
        "amount": {"minor_units": 129900, "currency": "INR"},
    }

    results: list[int] = []

    def _worker() -> int:
        client = ApiTestClient(app)
        resp = client.post_webhook("payment.captured", payload)
        return resp.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker) for _ in range(10)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    assert results.count(200) == 1, f"Expected exactly 1 200 OK, got: {results}"
    assert results.count(409) == 9, f"Expected 9 409 Conflict, got: {results}"

    # Verify attempt state is CAPTURED
    att = app.orchestrator._payment_repo.get("tenant_01", "att_race_sql_01")
    assert att is not None
    assert att.status == PaymentStatus.CAPTURED


def test_ap003_duplicate_failure_inventory_safety(tmp_path: Path) -> None:
    """Ensure duplicate payment.failed webhooks do not double-release inventory (§44, §61, AP-003)."""
    db_file = tmp_path / "webhook_failure_inventory.db"
    db_url = f"sqlite:///{db_file}"

    app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
    client = ApiTestClient(app)

    # Initial inventory: available=50, reserved=0
    assert app.orchestrator._inventory_repo is not None
    inv_initial = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_initial is not None
    assert inv_initial.available == 50
    assert inv_initial.reserved == 0

    # Reserve 2 items
    app.orchestrator._inventory_repo.reserve("tenant_01", "var_01", 2)
    inv_res = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_res is not None
    assert inv_res.reserved == 2

    # Seed attempt and order
    attempt = PaymentAttempt(
        attempt_id="att_fail_inv_01",
        intent_id="intent_fail_inv_01",
        tenant_id="tenant_01",
        amount=Money(20000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_fail_inv_01",
        provider="mock",
    )
    assert app.orchestrator._payment_repo is not None
    app.orchestrator._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_fail_inv_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(20000, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_fail_inv_01",
    )
    assert app.orchestrator._order_repo is not None
    app.orchestrator._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_fail_inv_safe_01",
        "event_type": "payment.failed",
        "attempt_id": "att_fail_inv_01",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_message": "Card declined",
    }

    # 1. First failure delivery -> releases reserved inventory
    r1 = client.post_webhook("payment.failed", payload)
    assert r1.status_code == 200

    inv_after_first = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_after_first is not None
    assert inv_after_first.reserved == 0

    # 2. Duplicate failure delivery -> rejected with 409 WEBHOOK_REPLAY_DETECTED
    r2 = client.post_webhook("payment.failed", payload)
    assert r2.status_code == 409
    assert r2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

    # Inventory must remain reserved == 0 (no negative reserved inventory!)
    inv_after_second = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_after_second is not None
    assert inv_after_second.reserved == 0


def test_ap003_duplicate_refund_inventory_safety(tmp_path: Path) -> None:
    """Ensure duplicate payment.refunded webhooks do not double-restock inventory (§44, §61, AP-003)."""
    db_file = tmp_path / "webhook_refund_inventory.db"
    db_url = f"sqlite:///{db_file}"

    app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
    client = ApiTestClient(app)

    # Initial inventory: reserve 2 items
    assert app.orchestrator._inventory_repo is not None
    app.orchestrator._inventory_repo.reserve("tenant_01", "var_01", 2)

    attempt = PaymentAttempt(
        attempt_id="att_ref_inv_01",
        intent_id="intent_ref_inv_01",
        tenant_id="tenant_01",
        amount=Money(20000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="idem_ref_inv_01",
        provider="mock",
    )
    assert app.orchestrator._payment_repo is not None
    app.orchestrator._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_ref_inv_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(20000, "INR"),
        status=OrderStatus.CONFIRMED,
        payment_attempt_id="att_ref_inv_01",
    )
    assert app.orchestrator._order_repo is not None
    app.orchestrator._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_ref_inv_safe_01",
        "event_type": "payment.refunded",
        "attempt_id": "att_ref_inv_01",
        "refund_id": "ref_001",
        "reason": "Customer cancellation",
    }

    # 1. First refund delivery -> releases reserved inventory
    r1 = client.post_webhook("payment.refunded", payload)
    assert r1.status_code == 200

    inv_after_first = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_after_first is not None
    assert inv_after_first.reserved == 0

    # 2. Duplicate refund delivery -> blocked with 409
    r2 = client.post_webhook("payment.refunded", payload)
    assert r2.status_code == 409
    assert r2.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

    inv_after_second = app.orchestrator._inventory_repo.get("tenant_01", "var_01")
    assert inv_after_second is not None
    assert inv_after_second.reserved == 0


def test_ap003_cross_provider_event_id_isolation() -> None:
    """Verify that identical event_ids from different providers do not collide (§61, AP-003)."""
    app, client, env = _seed_test_env()

    # Map Stripe payment
    mapping_stripe = ProviderTransactionMapping(
        mapping_id="map_stripe_iso",
        tenant_id="tenant_01",
        attempt_id="att_test_01",
        provider="stripe",
        provider_payment_id="pi_stripe_iso_01",
    )
    app.provider_mapping_repo.save(mapping_stripe)

    # Seed another attempt for Razorpay
    attempt_rzp = PaymentAttempt(
        attempt_id="att_rzp_01",
        intent_id="intent_rzp_01",
        tenant_id="tenant_01",
        amount=Money(129900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_att_rzp_01",
        provider="razorpay",
    )
    env["payment_repo"].save("tenant_01", attempt_rzp)

    mapping_rzp = ProviderTransactionMapping(
        mapping_id="map_rzp_iso",
        tenant_id="tenant_01",
        attempt_id="att_rzp_01",
        provider="razorpay",
        provider_payment_id="pay_rzp_iso_01",
    )
    app.provider_mapping_repo.save(mapping_rzp)

    shared_event_id = "evt_shared_cross_provider_001"

    # Stripe webhook with shared event_id
    stripe_payload = {
        "id": shared_event_id,
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_stripe_iso_01",
                "amount": 129900,
                "status": "succeeded",
            }
        },
    }
    r_stripe = client.post_stripe_webhook(stripe_payload)
    assert r_stripe.status_code == 200

    # Razorpay webhook with THE SAME event_id
    rzp_payload = {
        "id": shared_event_id,
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_iso_01",
                    "amount": 129900,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
    r_rzp = client.post_razorpay_webhook(rzp_payload)
    # Razorpay must succeed, not collide with Stripe's event!
    assert r_rzp.status_code == 200

    # Replay of Stripe must 409
    r_stripe_replay = client.post_stripe_webhook(stripe_payload)
    assert r_stripe_replay.status_code == 409
    assert r_stripe_replay.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

    # Replay of Razorpay must 409
    r_rzp_replay = client.post_razorpay_webhook(rzp_payload)
    assert r_rzp_replay.status_code == 409
    assert r_rzp_replay.body["error"]["code"] == "WEBHOOK_REPLAY_DETECTED"

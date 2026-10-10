"""Comprehensive test suite for Payment Webhook Ingestion, HMAC verification, replay defense, and reconciliation (§61)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.payment_repository import (
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
)
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.api.app import AgentPayApiApp, ApiTestClient
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

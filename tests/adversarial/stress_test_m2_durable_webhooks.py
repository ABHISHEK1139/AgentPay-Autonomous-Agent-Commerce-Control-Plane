"""Milestone 2 Empirical Durable Webhook Deduplication Stress Harness (§42, §43, §61, AP-003).

Exhaustively verifies durable webhook deduplication across SQLite instances:
1. Probe 1 (Restart Resilience):
   - Deliver valid webhook to initial app instance on persistent SQLite.
   - Re-instantiate fresh AgentPayApiApp with empty in-memory cache on same SQLite DB.
   - Replay identical webhook to second instance.
   - Verify HTTP 409 WEBHOOK_REPLAY_DETECTED and zero double-processing (both generic and provider routes).
2. Probe 2 (Multi-Instance Concurrency Race):
   - Launch concurrent threads delivering identical webhook to 4 separate app instances sharing SQLite DB.
   - Verify exactly 1 request succeeds (HTTP 200) and all concurrent requests return HTTP 409.
3. Probe 3 (Provider Event Namespace Isolation):
   - Deliver event with ID evt_universal_id from provider stripe.
   - Deliver event with identical ID evt_universal_id from provider razorpay.
   - Deliver event with identical ID evt_universal_id from provider mock.
   - Verify all 3 distinct providers do NOT collide and process cleanly, while replays return 409.
4. Probe 4 (SAGA Compensation Invariant Safety):
   - Deliver duplicate and concurrent payment.failed webhooks: verify single inventory release (never negative).
   - Deliver duplicate and concurrent payment.refunded webhooks: verify single refund (no duplicate restocking).
5. Probe 5 (Tampered Replay & Multi-Account Partitioning):
   - Tampered payload with identical event ID is rejected with 409 before state mutation.
   - Connected accounts (acct_A vs acct_B) with identical event IDs are isolated cleanly.
   - Failed webhook event replay is blocked with 409 WEBHOOK_REPLAY_DETECTED.
"""

from __future__ import annotations

import concurrent.futures
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from agentpay.api.app import ApiTestClient
from agentpay.api.factory import create_app
from agentpay.domain.commerce import Inventory, ProductVariant
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderTransactionMapping,
    WebhookEventStatus,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def probe_1_restart_resilience() -> dict[str, Any]:
    """Probe 1: Verify restart resilience across application restarts on durable SQLite storage."""
    violations: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_file = Path(tmp_dir) / "stress_m2_restart.db"
        db_url = f"sqlite:///{db_file}"

        # 1A. Generic webhook endpoint restart resilience
        app1 = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
        client1 = ApiTestClient(app1)

        attempt1 = PaymentAttempt(
            attempt_id="att_p1_res_01",
            intent_id="intent_p1_res_01",
            tenant_id="tenant_01",
            amount=Money(149900, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p1_res_01",
            provider="mock",
        )
        assert app1.orchestrator._payment_repo is not None
        app1.orchestrator._payment_repo.save("tenant_01", attempt1)

        order1 = Order(
            order_id="ord_p1_res_01",
            tenant_id="tenant_01",
            merchant_id="merch_01",
            buyer_id="user_01",
            agent_id="agent_01",
            variant_id="var_01",
            quantity=1,
            total_amount=Money(149900, "INR"),
            status=OrderStatus.PENDING_PAYMENT,
            payment_attempt_id="att_p1_res_01",
        )
        assert app1.orchestrator._order_repo is not None
        app1.orchestrator._order_repo.save("tenant_01", order1)

        payload1 = {
            "event_id": "evt_p1_restart_generic_01",
            "event_type": "payment.captured",
            "attempt_id": "att_p1_res_01",
            "provider": "mock",
            "amount": {"minor_units": 149900, "currency": "INR"},
        }

        r1 = client1.post_webhook("payment.captured", payload1)
        if r1.status_code != 200:
            violations.append(
                f"Generic webhook initial delivery failed with {r1.status_code}: {r1.body}"
            )

        # Simulate restart: spin up completely separate App 2 on same DB
        app2 = create_app(database_url=db_url, seed_demo_data=False, tenant_id="tenant_01")
        client2 = ApiTestClient(app2)

        if len(app2._processed_webhook_events) != 0:
            violations.append(
                f"App 2 in-memory cache was not empty on cold restart (size: {len(app2._processed_webhook_events)})"
            )

        # Replay identical webhook against App 2
        r2 = client2.post_webhook("payment.captured", payload1)
        if r2.status_code != 409:
            violations.append(
                f"Generic webhook replay against App 2 returned {r2.status_code}, expected 409"
            )
        if r2.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
            violations.append(
                f"Expected error code WEBHOOK_REPLAY_DETECTED, got {r2.body.get('error', {}).get('code')}"
            )

        # Verify App 2 L1 cache populated after DB detection
        cache_key = "mock::evt_p1_restart_generic_01"
        if cache_key not in app2._processed_webhook_events:
            violations.append(
                f"Cache key '{cache_key}' not populated in App 2 L1 cache after replay"
            )

        # 1B. Stripe provider-specific route restart resilience
        mapping_stripe = ProviderTransactionMapping(
            mapping_id="map_p1_stripe",
            tenant_id="tenant_01",
            attempt_id="att_p1_res_01",
            provider="stripe",
            provider_payment_id="pi_p1_stripe_res_01",
        )
        app1.provider_mapping_repo.save(mapping_stripe)

        stripe_payload = {
            "id": "evt_p1_restart_stripe_01",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_p1_stripe_res_01",
                    "amount": 149900,
                    "status": "succeeded",
                }
            },
        }

        r_stripe_1 = client1.post_stripe_webhook(stripe_payload)
        if r_stripe_1.status_code != 200:
            violations.append(
                f"Stripe initial delivery failed: {r_stripe_1.status_code}: {r_stripe_1.body}"
            )

        # Simulate fresh App 3
        app3 = create_app(database_url=db_url, seed_demo_data=False, tenant_id="tenant_01")
        client3 = ApiTestClient(app3)

        if len(app3._processed_webhook_events) != 0:
            violations.append("App 3 L1 cache not empty on restart")

        r_stripe_2 = client3.post_stripe_webhook(stripe_payload)
        if r_stripe_2.status_code != 409:
            violations.append(
                f"Stripe replay against App 3 returned {r_stripe_2.status_code}, expected 409"
            )
        if r_stripe_2.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
            violations.append(
                f"Stripe replay expected WEBHOOK_REPLAY_DETECTED, got {r_stripe_2.body}"
            )

        # Verify durable DB record status
        assert app3.webhook_event_repo is not None
        rec_stripe = app3.webhook_event_repo.find_by_provider_event(
            "stripe", "evt_p1_restart_stripe_01"
        )
        if rec_stripe is None or rec_stripe.status != WebhookEventStatus.PROCESSED:
            violations.append(f"Stripe durable DB record missing or not PROCESSED: {rec_stripe}")

    return {
        "probe": "probe_1_restart_resilience",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_2_multi_instance_concurrency_race() -> dict[str, Any]:
    """Probe 2: Multi-instance concurrency race across separate application nodes sharing SQLite DB."""
    violations: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_file = Path(tmp_dir) / "stress_m2_multi_race.db"
        db_url = f"sqlite:///{db_file}"

        # Spin up 4 distinct application instances sharing the database
        app_nodes = [
            create_app(database_url=db_url, seed_demo_data=(i == 0), tenant_id="tenant_01")
            for i in range(4)
        ]

        attempt = PaymentAttempt(
            attempt_id="att_p2_race_01",
            intent_id="intent_p2_race_01",
            tenant_id="tenant_01",
            amount=Money(99900, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p2_race_01",
            provider="mock",
        )
        assert app_nodes[0].orchestrator._payment_repo is not None
        app_nodes[0].orchestrator._payment_repo.save("tenant_01", attempt)

        order = Order(
            order_id="ord_p2_race_01",
            tenant_id="tenant_01",
            merchant_id="merch_01",
            buyer_id="user_01",
            agent_id="agent_01",
            variant_id="var_01",
            quantity=1,
            total_amount=Money(99900, "INR"),
            status=OrderStatus.PENDING_PAYMENT,
            payment_attempt_id="att_p2_race_01",
        )
        assert app_nodes[0].orchestrator._order_repo is not None
        app_nodes[0].orchestrator._order_repo.save("tenant_01", order)

        payload = {
            "event_id": "evt_p2_multi_race_999",
            "event_type": "payment.captured",
            "attempt_id": "att_p2_race_01",
            "provider": "mock",
            "amount": {"minor_units": 99900, "currency": "INR"},
        }

        concurrency = 24
        barrier = threading.Barrier(concurrency)
        results: list[tuple[int, str]] = []

        def _race_worker(worker_id: int) -> tuple[int, str]:
            app = app_nodes[worker_id % len(app_nodes)]
            client = ApiTestClient(app)
            barrier.wait()
            resp = client.post_webhook("payment.captured", payload)
            err_code = resp.body.get("error", {}).get("code", "")
            return resp.status_code, err_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_race_worker, i) for i in range(concurrency)]
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())

        status_codes = [r[0] for r in results]
        err_codes = [r[1] for r in results]

        success_count = status_codes.count(200)
        conflict_count = status_codes.count(409)

        if success_count != 1:
            violations.append(
                f"Expected exactly 1 success (200), observed {success_count} (out of {concurrency})"
            )
        if conflict_count != concurrency - 1:
            violations.append(
                f"Expected exactly {concurrency - 1} conflicts (409), observed {conflict_count} (out of {concurrency})"
            )

        replay_err_count = err_codes.count("WEBHOOK_REPLAY_DETECTED")
        if replay_err_count != concurrency - 1:
            violations.append(
                f"Expected {concurrency - 1} WEBHOOK_REPLAY_DETECTED errors, got {replay_err_count}"
            )

        # Verify underlying payment attempt and order status
        saved_att = app_nodes[0].orchestrator._payment_repo.get("tenant_01", "att_p2_race_01")
        if saved_att is None or saved_att.status != PaymentStatus.CAPTURED:
            violations.append(f"Payment attempt not CAPTURED: {saved_att}")

        saved_ord = app_nodes[0].orchestrator._order_repo.get("tenant_01", "ord_p2_race_01")
        if saved_ord is None or saved_ord.status != OrderStatus.CONFIRMED:
            violations.append(f"Order not CONFIRMED: {saved_ord}")

        # Verify database uniqueness constraint maintained exactly 1 durable record
        assert app_nodes[0].webhook_event_repo is not None
        db_ev = app_nodes[0].webhook_event_repo.find_by_provider_event(
            "mock", "evt_p2_multi_race_999"
        )
        if db_ev is None or db_ev.status != WebhookEventStatus.PROCESSED:
            violations.append(f"Durable event record corrupted or not PROCESSED: {db_ev}")

    return {
        "probe": "probe_2_multi_instance_concurrency_race",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_3_provider_event_namespace_isolation() -> dict[str, Any]:
    """Probe 3: Verify provider event namespace isolation (identical event_id across distinct providers)."""
    violations: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_file = Path(tmp_dir) / "stress_m2_namespace.db"
        db_url = f"sqlite:///{db_file}"

        app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
        client = ApiTestClient(app)

        # 1. Stripe payment attempt & mapping
        attempt_stripe = PaymentAttempt(
            attempt_id="att_p3_stripe_01",
            intent_id="intent_p3_stripe_01",
            tenant_id="tenant_01",
            amount=Money(120000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p3_stripe_01",
            provider="stripe",
        )
        assert app.orchestrator._payment_repo is not None
        app.orchestrator._payment_repo.save("tenant_01", attempt_stripe)

        mapping_stripe = ProviderTransactionMapping(
            mapping_id="map_p3_stripe_01",
            tenant_id="tenant_01",
            attempt_id="att_p3_stripe_01",
            provider="stripe",
            provider_payment_id="pi_p3_stripe_01",
        )
        app.provider_mapping_repo.save(mapping_stripe)

        # 2. Razorpay payment attempt & mapping
        attempt_rzp = PaymentAttempt(
            attempt_id="att_p3_rzp_01",
            intent_id="intent_p3_rzp_01",
            tenant_id="tenant_01",
            amount=Money(150000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p3_rzp_01",
            provider="razorpay",
        )
        app.orchestrator._payment_repo.save("tenant_01", attempt_rzp)

        mapping_rzp = ProviderTransactionMapping(
            mapping_id="map_p3_rzp_01",
            tenant_id="tenant_01",
            attempt_id="att_p3_rzp_01",
            provider="razorpay",
            provider_payment_id="pay_p3_rzp_01",
        )
        app.provider_mapping_repo.save(mapping_rzp)

        # 3. Generic mock payment attempt
        attempt_mock = PaymentAttempt(
            attempt_id="att_p3_mock_01",
            intent_id="intent_p3_mock_01",
            tenant_id="tenant_01",
            amount=Money(80000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p3_mock_01",
            provider="mock",
        )
        app.orchestrator._payment_repo.save("tenant_01", attempt_mock)

        # Universal identical event ID across all 3 providers
        universal_event_id = "evt_universal_collision_test_001"

        # A. Deliver Stripe event
        stripe_payload = {
            "id": universal_event_id,
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_p3_stripe_01",
                    "amount": 120000,
                    "status": "succeeded",
                }
            },
        }
        r_stripe = client.post_stripe_webhook(stripe_payload)
        if r_stripe.status_code != 200:
            violations.append(
                f"Stripe initial delivery failed: {r_stripe.status_code}: {r_stripe.body}"
            )

        # B. Deliver Razorpay event with IDENTICAL event_id
        rzp_payload = {
            "id": universal_event_id,
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_p3_rzp_01",
                        "amount": 150000,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
        r_rzp = client.post_razorpay_webhook(rzp_payload)
        if r_rzp.status_code != 200:
            violations.append(
                f"Razorpay collided with Stripe or failed: {r_rzp.status_code}: {r_rzp.body}"
            )

        # C. Deliver Generic Mock event with IDENTICAL event_id
        mock_payload = {
            "event_id": universal_event_id,
            "event_type": "payment.captured",
            "attempt_id": "att_p3_mock_01",
            "provider": "mock",
            "amount": {"minor_units": 80000, "currency": "INR"},
        }
        r_mock = client.post_webhook("payment.captured", mock_payload)
        if r_mock.status_code != 200:
            violations.append(
                f"Mock collided with Stripe/Razorpay or failed: {r_mock.status_code}: {r_mock.body}"
            )

        # D. Now verify replay against each distinct provider yields 409
        r_stripe_rep = client.post_stripe_webhook(stripe_payload)
        if r_stripe_rep.status_code != 409:
            violations.append(f"Stripe replay returned {r_stripe_rep.status_code}, expected 409")

        r_rzp_rep = client.post_razorpay_webhook(rzp_payload)
        if r_rzp_rep.status_code != 409:
            violations.append(f"Razorpay replay returned {r_rzp_rep.status_code}, expected 409")

        r_mock_rep = client.post_webhook("payment.captured", mock_payload)
        if r_mock_rep.status_code != 409:
            violations.append(f"Mock replay returned {r_mock_rep.status_code}, expected 409")

        # E. Verify all 3 events are stored distinctly in the repository
        assert app.webhook_event_repo is not None
        rec_stripe = app.webhook_event_repo.find_by_provider_event("stripe", universal_event_id)
        rec_rzp = app.webhook_event_repo.find_by_provider_event("razorpay", universal_event_id)
        rec_mock = app.webhook_event_repo.find_by_provider_event("mock", universal_event_id)

        if not rec_stripe or rec_stripe.provider != "stripe":
            violations.append("Stripe durable record missing or provider mismatch")
        if not rec_rzp or rec_rzp.provider != "razorpay":
            violations.append("Razorpay durable record missing or provider mismatch")
        if not rec_mock or rec_mock.provider != "mock":
            violations.append("Mock durable record missing or provider mismatch")

    return {
        "probe": "probe_3_provider_event_namespace_isolation",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_4_saga_compensation_invariants() -> dict[str, Any]:
    """Probe 4: SAGA compensation invariant safety under duplicate/concurrent failure and refund webhooks."""
    violations: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_file = Path(tmp_dir) / "stress_m2_saga.db"
        db_url = f"sqlite:///{db_file}"

        app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
        client = ApiTestClient(app)

        # 4A. payment.failed duplicate burst -> inventory must release exactly once (never negative)
        assert app.orchestrator._inventory_repo is not None
        assert app.orchestrator._variant_repo is not None
        var_id = "var_saga_fail_01"
        app.orchestrator._variant_repo.save(
            "tenant_01",
            ProductVariant(var_id, "prod_1", "SKU_SAGA_1", "Item SAGA", Money(1000, "INR")),
        )
        app.orchestrator._inventory_repo.save(
            "tenant_01", Inventory(var_id, "tenant_01", available=100, reserved=0)
        )

        # Reserve 5 items
        app.orchestrator._inventory_repo.reserve("tenant_01", var_id, 5)
        inv_before = app.orchestrator._inventory_repo.get("tenant_01", var_id)
        if inv_before is None or inv_before.reserved != 5:
            violations.append(f"Initial reserved inventory failed setup: {inv_before}")

        attempt_fail = PaymentAttempt(
            attempt_id="att_saga_fail_01",
            intent_id="intent_saga_fail_01",
            tenant_id="tenant_01",
            amount=Money(5000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_saga_fail_01",
            provider="mock",
        )
        assert app.orchestrator._payment_repo is not None
        app.orchestrator._payment_repo.save("tenant_01", attempt_fail)

        order_fail = Order(
            order_id="ord_saga_fail_01",
            tenant_id="tenant_01",
            merchant_id="merch_01",
            buyer_id="user_01",
            agent_id="agent_01",
            variant_id=var_id,
            quantity=5,
            total_amount=Money(5000, "INR"),
            status=OrderStatus.PENDING_PAYMENT,
            payment_attempt_id="att_saga_fail_01",
        )
        assert app.orchestrator._order_repo is not None
        app.orchestrator._order_repo.save("tenant_01", order_fail)

        fail_payload = {
            "event_id": "evt_saga_fail_burst_001",
            "event_type": "payment.failed",
            "attempt_id": "att_saga_fail_01",
            "error_code": "CARD_DECLINED",
            "error_message": "Insufficient funds",
        }

        # Concurrently blast 20 duplicate failure deliveries
        concurrency = 20
        barrier = threading.Barrier(concurrency)
        codes: list[int] = []

        def _fail_worker() -> int:
            barrier.wait()
            resp = client.post_webhook("payment.failed", fail_payload)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_fail_worker) for _ in range(concurrency)]
            for fut in concurrent.futures.as_completed(futures):
                codes.append(fut.result())

        if codes.count(200) != 1:
            violations.append(
                f"payment.failed concurrent burst: expected 1x 200, got {codes.count(200)}"
            )
        if codes.count(409) != concurrency - 1:
            violations.append(
                f"payment.failed concurrent burst: expected {concurrency - 1}x 409, got {codes.count(409)}"
            )

        # Inventory check: reserved MUST be exactly 0, never negative!
        inv_after_fail = app.orchestrator._inventory_repo.get("tenant_01", var_id)
        if inv_after_fail is None:
            violations.append("Inventory record missing after failure burst")
        else:
            if inv_after_fail.reserved != 0:
                violations.append(
                    f"INVARIANT VIOLATION: reserved inventory is {inv_after_fail.reserved}, expected 0"
                )
            if inv_after_fail.available != 100:
                violations.append(
                    f"INVARIANT VIOLATION: available inventory improperly modified: {inv_after_fail.available}, expected 100"
                )

        # 4B. payment.refunded duplicate burst -> refund processed exactly once
        var_ref_id = "var_saga_ref_01"
        app.orchestrator._variant_repo.save(
            "tenant_01",
            ProductVariant(var_ref_id, "prod_1", "SKU_SAGA_2", "Item Ref", Money(2000, "INR")),
        )
        app.orchestrator._inventory_repo.save(
            "tenant_01", Inventory(var_ref_id, "tenant_01", available=50, reserved=3)
        )

        attempt_ref = PaymentAttempt(
            attempt_id="att_saga_ref_01",
            intent_id="intent_saga_ref_01",
            tenant_id="tenant_01",
            amount=Money(6000, "INR"),
            status=PaymentStatus.CAPTURED,
            idempotency_key="idem_saga_ref_01",
            provider="mock",
        )
        app.orchestrator._payment_repo.save("tenant_01", attempt_ref)

        order_ref = Order(
            order_id="ord_saga_ref_01",
            tenant_id="tenant_01",
            merchant_id="merch_01",
            buyer_id="user_01",
            agent_id="agent_01",
            variant_id=var_ref_id,
            quantity=3,
            total_amount=Money(6000, "INR"),
            status=OrderStatus.CONFIRMED,
            payment_attempt_id="att_saga_ref_01",
        )
        app.orchestrator._order_repo.save("tenant_01", order_ref)

        ref_payload = {
            "event_id": "evt_saga_ref_burst_001",
            "event_type": "payment.refunded",
            "attempt_id": "att_saga_ref_01",
            "refund_id": "rf_saga_001",
            "reason": "Customer cancellation",
        }

        barrier_ref = threading.Barrier(concurrency)
        ref_codes: list[int] = []

        def _ref_worker() -> int:
            barrier_ref.wait()
            resp = client.post_webhook("payment.refunded", ref_payload)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_ref_worker) for _ in range(concurrency)]
            for fut in concurrent.futures.as_completed(futures):
                ref_codes.append(fut.result())

        if ref_codes.count(200) != 1:
            violations.append(
                f"payment.refunded concurrent burst: expected 1x 200, got {ref_codes.count(200)}"
            )
        if ref_codes.count(409) != concurrency - 1:
            violations.append(
                f"payment.refunded concurrent burst: expected {concurrency - 1}x 409, got {ref_codes.count(409)}"
            )

        saved_ref_att = app.orchestrator._payment_repo.get("tenant_01", "att_saga_ref_01")
        if saved_ref_att is None or saved_ref_att.status != PaymentStatus.REFUNDED:
            violations.append(f"Payment attempt not REFUNDED: {saved_ref_att}")

        inv_after_ref = app.orchestrator._inventory_repo.get("tenant_01", var_ref_id)
        if inv_after_ref is None or inv_after_ref.reserved != 0:
            violations.append(f"Inventory reserved count incorrect after refund: {inv_after_ref}")

    return {
        "probe": "probe_4_saga_compensation_invariants",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def probe_5_tampered_payload_and_account_partitioning() -> dict[str, Any]:
    """Probe 5: Adversarial edge cases: tampered payload replay, account ID partitioning, failed state replay."""
    violations: list[str] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_file = Path(tmp_dir) / "stress_m2_edge_cases.db"
        db_url = f"sqlite:///{db_file}"

        app = create_app(database_url=db_url, seed_demo_data=True, tenant_id="tenant_01")
        client = ApiTestClient(app)

        # 5A. Tampered Payload Replay: Attacker submits same event_id but altered amount and victim attempt
        attempt_legit = PaymentAttempt(
            attempt_id="att_p5_legit_01",
            intent_id="intent_p5_legit_01",
            tenant_id="tenant_01",
            amount=Money(500, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p5_legit_01",
            provider="mock",
        )
        assert app.orchestrator._payment_repo is not None
        app.orchestrator._payment_repo.save("tenant_01", attempt_legit)

        attempt_victim = PaymentAttempt(
            attempt_id="att_p5_victim_02",
            intent_id="intent_p5_victim_02",
            tenant_id="tenant_01",
            amount=Money(1000000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p5_victim_02",
            provider="mock",
        )
        app.orchestrator._payment_repo.save("tenant_01", attempt_victim)

        legit_payload = {
            "event_id": "evt_p5_tamper_target_001",
            "event_type": "payment.captured",
            "attempt_id": "att_p5_legit_01",
            "provider": "mock",
            "amount": {"minor_units": 500, "currency": "INR"},
        }
        r_legit = client.post_webhook("payment.captured", legit_payload)
        if r_legit.status_code != 200:
            violations.append(f"Legitimate webhook failed: {r_legit.status_code}")

        # Tampered replay targeting victim
        tampered_payload = {
            "event_id": "evt_p5_tamper_target_001",
            "event_type": "payment.captured",
            "attempt_id": "att_p5_victim_02",
            "provider": "mock",
            "amount": {"minor_units": 1000000, "currency": "INR"},
        }
        r_tamper = client.post_webhook("payment.captured", tampered_payload)
        if r_tamper.status_code != 409:
            violations.append(f"Tampered replay returned {r_tamper.status_code}, expected 409")
        if r_tamper.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
            violations.append(
                f"Expected WEBHOOK_REPLAY_DETECTED on tampered replay, got {r_tamper.body}"
            )

        # Ensure victim attempt was NOT transitioned to CAPTURED
        victim_check = app.orchestrator._payment_repo.get("tenant_01", "att_p5_victim_02")
        if victim_check is None or victim_check.status != PaymentStatus.CREATED:
            violations.append(
                f"SECURITY VIOLATION: victim attempt modified by tampered replay! Status: {victim_check}"
            )

        # 5B. Connected Accounts Isolation (acct_A vs acct_B)
        mapping_acct_a = ProviderTransactionMapping(
            mapping_id="map_acct_a",
            tenant_id="tenant_01",
            attempt_id="att_p5_legit_01",
            provider="stripe",
            provider_payment_id="pi_stripe_acct_a",
        )
        app.provider_mapping_repo.save(mapping_acct_a)

        attempt_acct_b = PaymentAttempt(
            attempt_id="att_p5_acct_b",
            intent_id="intent_p5_acct_b",
            tenant_id="tenant_01",
            amount=Money(7000, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="idem_p5_acct_b",
            provider="stripe",
        )
        app.orchestrator._payment_repo.save("tenant_01", attempt_acct_b)

        mapping_acct_b = ProviderTransactionMapping(
            mapping_id="map_acct_b",
            tenant_id="tenant_01",
            attempt_id="att_p5_acct_b",
            provider="stripe",
            provider_payment_id="pi_stripe_acct_b",
        )
        app.provider_mapping_repo.save(mapping_acct_b)

        shared_event_id = "evt_stripe_connect_shared_001"

        payload_acct_a = {
            "id": shared_event_id,
            "account": "acct_merchant_A",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_stripe_acct_a",
                    "amount": 500,
                    "status": "succeeded",
                }
            },
        }
        r_acct_a = client.post_stripe_webhook(payload_acct_a)
        if r_acct_a.status_code != 200:
            violations.append(
                f"Stripe account A delivery failed: {r_acct_a.status_code}: {r_acct_a.body}"
            )

        # Account B delivers SAME event ID
        payload_acct_b = {
            "id": shared_event_id,
            "account": "acct_merchant_B",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_stripe_acct_b",
                    "amount": 7000,
                    "status": "succeeded",
                }
            },
        }
        r_acct_b = client.post_stripe_webhook(payload_acct_b)
        if r_acct_b.status_code != 200:
            violations.append(
                f"Stripe account B collided with account A or failed: {r_acct_b.status_code}: {r_acct_b.body}"
            )

        # Replay against Account A must 409
        r_acct_a_rep = client.post_stripe_webhook(payload_acct_a)
        if r_acct_a_rep.status_code != 409:
            violations.append(f"Account A replay returned {r_acct_a_rep.status_code}, expected 409")

        # Replay against Account B must 409
        r_acct_b_rep = client.post_stripe_webhook(payload_acct_b)
        if r_acct_b_rep.status_code != 409:
            violations.append(f"Account B replay returned {r_acct_b_rep.status_code}, expected 409")

        # 5C. Replay of Failed Event: ensures events that failed reconciliation cannot be replayed arbitrarily
        failed_event_payload = {
            "event_id": "evt_p5_failed_event_001",
            "event_type": "payment.captured",
            "attempt_id": "att_non_existent_9999",  # Will fail reconciliation
            "provider": "mock",
            "amount": {"minor_units": 1000, "currency": "INR"},
        }
        r_fail_initial = client.post_webhook("payment.captured", failed_event_payload)
        # Should fail with 400 ATTEMPT_NOT_FOUND
        if r_fail_initial.status_code != 400:
            violations.append(
                f"Expected 400 on initial non-existent attempt, got {r_fail_initial.status_code}"
            )

        # Verify event was durably marked as FAILED in database
        assert app.webhook_event_repo is not None
        failed_rec = app.webhook_event_repo.find_by_provider_event(
            "mock", "evt_p5_failed_event_001"
        )
        if failed_rec is None or failed_rec.status != WebhookEventStatus.FAILED:
            violations.append(f"Event was not saved with FAILED status: {failed_rec}")

        # Now re-submit the exact same failed event: must return 409 WEBHOOK_REPLAY_DETECTED
        r_fail_rep = client.post_webhook("payment.captured", failed_event_payload)
        if r_fail_rep.status_code != 409:
            violations.append(
                f"Replay of failed event returned {r_fail_rep.status_code}, expected 409 WEBHOOK_REPLAY_DETECTED"
            )
        if r_fail_rep.body.get("error", {}).get("code") != "WEBHOOK_REPLAY_DETECTED":
            violations.append(
                f"Expected WEBHOOK_REPLAY_DETECTED for failed event replay: {r_fail_rep.body}"
            )

    return {
        "probe": "probe_5_tampered_payload_and_account_partitioning",
        "passed": len(violations) == 0,
        "violations": violations,
    }


def main() -> int:
    log("=" * 72)
    log("AGENTPAY EMPIRICAL AP-003 DURABLE WEBHOOK DEDUPLICATION ADVERSARIAL HARNESS")
    log("=" * 72)

    probes = [
        ("probe_1_restart_resilience", probe_1_restart_resilience),
        ("probe_2_multi_instance_concurrency_race", probe_2_multi_instance_concurrency_race),
        ("probe_3_provider_event_namespace_isolation", probe_3_provider_event_namespace_isolation),
        ("probe_4_saga_compensation_invariants", probe_4_saga_compensation_invariants),
        (
            "probe_5_tampered_payload_and_account_partitioning",
            probe_5_tampered_payload_and_account_partitioning,
        ),
    ]

    all_passed = True
    for name, fn in probes:
        res = fn()
        if res.get("passed"):
            log(f"[*] {name}: PASS")
        else:
            log(f"[!] {name}: FAIL -> {res.get('violations')}")
            all_passed = False

    log("=" * 72)
    if all_passed:
        log("[+] VERDICT: ALL DURABLE DEDUPLICATION INVARIANTS SATISFIED -> APPROVE")
        return 0
    else:
        log("[-] VERDICT: VULNERABILITIES DETECTED -> REJECT")
        return 1


if __name__ == "__main__":
    sys.exit(main())

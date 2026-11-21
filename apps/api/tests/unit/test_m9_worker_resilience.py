"""Unit and integration tests for Milestone M9: Worker Runtime, Webhook Replay & Resilience Hardening (§104, §105, §106, AP-003).

Covers:
- Feature 38: CLI Worker Outbox Repository Wiring
- Feature 39: Implement `list_by_status` on `SqlAlchemyPaymentRepository` & port protocol
- Feature 40: Approval Expiry TOCTOU Race Condition Fix
- Feature 41: Worker Payment Reconciliation Backoff & Retry Threshold
- Feature 42: EventBus Memory Leak Mitigation (Bounded Cache)
- Feature 43: Webhook Transient Replay Lockout Fix
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.outbox_repository import InMemoryOutboxRepository
from agentpay.adapters.in_memory.payment_repository import (
    InMemoryPaymentRepository,
    InMemoryProviderWebhookEventRepository,
)
from agentpay.api.app import ApiTestClient
from agentpay.api.factory import create_app
from agentpay.application.ports.payment_port import PaymentRepository
from agentpay.application.services.event_bus import EventBus
from agentpay.cli import _handle_worker
from agentpay.domain.approval import Approval, ApprovalBinding, ApprovalStatus
from agentpay.domain.money import Money
from agentpay.domain.outbox import OutboxEvent
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    WebhookEventStatus,
)
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.approval_repository import (
    SqlAlchemyApprovalRepository,
)
from agentpay.infrastructure.database.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)
from agentpay.infrastructure.database.repositories.payment_repository import (
    SqlAlchemyPaymentRepository,
)
from agentpay.infrastructure.database.repositories.webhook_event_repository import (
    SqlAlchemyProviderWebhookEventRepository,
)
from agentpay.workers.worker import BackgroundWorker

# ==============================================================================
# Feature 38: CLI Worker Outbox Repository Wiring
# ==============================================================================


def test_cli_worker_outbox_wiring_with_database() -> None:
    """Verify CLI worker wires SqlAlchemyOutboxRepository when database_url is provided."""
    args = argparse.Namespace(interval=1, db="sqlite:///:memory:")

    with (
        patch("agentpay.cli.BackgroundWorker") as mock_bw_cls,
        patch("signal.signal"),
        patch("time.sleep", side_effect=KeyboardInterrupt),
    ):
        mock_worker = MagicMock()
        mock_bw_cls.return_value = mock_worker
        with contextlib.suppress(KeyboardInterrupt):
            _handle_worker(args)

        assert mock_bw_cls.called
        call_kwargs = mock_bw_cls.call_args[1]
        outbox_repo = call_kwargs.get("outbox_repo")
        assert isinstance(outbox_repo, SqlAlchemyOutboxRepository)


def test_cli_worker_outbox_wiring_without_database() -> None:
    """Verify CLI worker safely falls back to InMemoryOutboxRepository when no DB is configured."""
    args = argparse.Namespace(interval=1, db=None)

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("agentpay.cli.BackgroundWorker") as mock_bw_cls,
        patch("signal.signal"),
        patch("time.sleep", side_effect=KeyboardInterrupt),
    ):
        mock_worker = MagicMock()
        mock_bw_cls.return_value = mock_worker
        with contextlib.suppress(KeyboardInterrupt):
            _handle_worker(args)

        assert mock_bw_cls.called
        call_kwargs = mock_bw_cls.call_args[1]
        outbox_repo = call_kwargs.get("outbox_repo")
        assert isinstance(outbox_repo, InMemoryOutboxRepository)


# ==============================================================================
# Feature 39: list_by_status on SqlAlchemyPaymentRepository and Port Protocol
# ==============================================================================


def test_payment_repository_protocol_compliance() -> None:
    """Ensure both InMemoryPaymentRepository and SqlAlchemyPaymentRepository satisfy PaymentRepository port."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    sql_repo = SqlAlchemyPaymentRepository(session_factory)
    in_mem_repo = InMemoryPaymentRepository()

    assert isinstance(sql_repo, PaymentRepository)
    assert isinstance(in_mem_repo, PaymentRepository)
    assert hasattr(sql_repo, "list_by_status")
    assert hasattr(in_mem_repo, "list_by_status")


def test_sqlalchemy_payment_repository_list_by_status() -> None:
    """Verify list_by_status filters by status and optional tenant_id in relational DB."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    repo = SqlAlchemyPaymentRepository(session_factory)

    now = datetime.now(UTC)
    att1 = PaymentAttempt(
        attempt_id="att_01",
        intent_id="int_01",
        tenant_id="tenant_a",
        amount=Money(1000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="key_01",
        provider="mock",
        created_at=now - timedelta(minutes=10),
    )
    att2 = PaymentAttempt(
        attempt_id="att_02",
        intent_id="int_02",
        tenant_id="tenant_a",
        amount=Money(2000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="key_02",
        provider="mock",
        created_at=now - timedelta(minutes=5),
    )
    att3 = PaymentAttempt(
        attempt_id="att_03",
        intent_id="int_03",
        tenant_id="tenant_b",
        amount=Money(3000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="key_03",
        provider="mock",
        created_at=now - timedelta(minutes=2),
    )

    repo.save("tenant_a", att1)
    repo.save("tenant_a", att2)
    repo.save("tenant_b", att3)

    # 1. Query all UNKNOWN across tenants
    unknown_all = repo.list_by_status(PaymentStatus.UNKNOWN)
    assert len(unknown_all) == 2
    attempt_ids = {a.attempt_id for a in unknown_all}
    assert attempt_ids == {"att_01", "att_03"}
    for item in unknown_all:
        assert isinstance(item, PaymentAttempt)
        assert item.status == PaymentStatus.UNKNOWN

    # 2. Query UNKNOWN scoped to tenant_a
    unknown_tenant_a = repo.list_by_status(PaymentStatus.UNKNOWN, tenant_id="tenant_a")
    assert len(unknown_tenant_a) == 1
    assert unknown_tenant_a[0].attempt_id == "att_01"

    # 3. Query CAPTURED
    captured_all = repo.list_by_status(PaymentStatus.CAPTURED)
    assert len(captured_all) == 1
    assert captured_all[0].attempt_id == "att_02"


def test_in_memory_payment_repository_list_by_status() -> None:
    """Verify list_by_status filters correctly on in-memory repository."""
    repo = InMemoryPaymentRepository()
    now = datetime.now(UTC)

    att1 = PaymentAttempt(
        attempt_id="att_mem_1",
        intent_id="int_1",
        tenant_id="tenant_1",
        amount=Money(100, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="k1",
        provider="mock",
        created_at=now,
    )
    att2 = PaymentAttempt(
        attempt_id="att_mem_2",
        intent_id="int_2",
        tenant_id="tenant_2",
        amount=Money(200, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="k2",
        provider="mock",
        created_at=now,
    )
    att3 = PaymentAttempt(
        attempt_id="att_mem_3",
        intent_id="int_3",
        tenant_id="tenant_1",
        amount=Money(300, "INR"),
        status=PaymentStatus.AUTHORIZED,
        idempotency_key="k3",
        provider="mock",
        created_at=now,
    )
    repo.save("tenant_1", att1)
    repo.save("tenant_2", att2)
    repo.save("tenant_1", att3)

    all_unknown = repo.list_by_status(PaymentStatus.UNKNOWN)
    assert len(all_unknown) == 2

    tenant_1_unknown = repo.list_by_status(PaymentStatus.UNKNOWN, tenant_id="tenant_1")
    assert len(tenant_1_unknown) == 1
    assert tenant_1_unknown[0].attempt_id == "att_mem_1"


# ==============================================================================
# Feature 40: Approval Expiry TOCTOU Race Condition Fix
# ==============================================================================


def test_approval_expiry_toctou_operator_resolution_wins() -> None:
    """Verify worker passes expected_status=PENDING and gracefully ignores ApprovalConcurrencyError.

    When an operator concurrently approves an action, background expiration must NOT
    overwrite APPROVED with EXPIRED.
    """
    approval_repo = InMemoryApprovalRepository()
    now = datetime.now(UTC)
    stale_time = now - timedelta(hours=2)

    binding = ApprovalBinding(
        action_id="act_race_01",
        amount=Money(500000, "INR"),
        currency="INR",
        merchant_id="merch_01",
        resource_id="prod_01",
        action_version=1,
        tenant_id="tenant_01",
        agent_id="agent_01",
        principal_id="user_01",
    )
    app = Approval(
        approval_id="app_race_01",
        tenant_id="tenant_01",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=stale_time,
    )
    approval_repo.save("tenant_01", app)

    worker = BackgroundWorker(approval_repo=approval_repo, approval_ttl_seconds=3600)

    # Concurrently resolve the approval to APPROVED by operator
    approved_app = app.approve(approver_id="usr_operator", decided_at=now)
    approval_repo.save("tenant_01", approved_app)

    # Now execute worker expiration sweep
    expired_count = worker._expire_stale_approvals()

    assert expired_count == 0
    persisted = approval_repo.get("tenant_01", "app_race_01")
    assert persisted is not None
    assert persisted.status == ApprovalStatus.APPROVED
    assert persisted.approver_id == "usr_operator"


def test_approval_expiry_sqlalchemy_concurrency_protection() -> None:
    """Verify conditional update protects operator decision in relational database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    approval_repo = SqlAlchemyApprovalRepository(session_factory)

    now = datetime.now(UTC)
    stale_time = now - timedelta(hours=2)

    binding = ApprovalBinding(
        action_id="act_sql_01",
        amount=Money(500000, "INR"),
        currency="INR",
        merchant_id="merch_01",
        resource_id="prod_01",
        action_version=1,
        tenant_id="tenant_01",
        agent_id="agent_01",
        principal_id="user_01",
    )
    app = Approval(
        approval_id="app_sql_01",
        tenant_id="tenant_01",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=stale_time,
    )
    approval_repo.save("tenant_01", app)

    # Operator approves in database
    approved = app.approve("usr_manager", decided_at=now)
    approval_repo.save("tenant_01", approved)

    worker = BackgroundWorker(approval_repo=approval_repo, approval_ttl_seconds=3600)
    expired = worker._expire_stale_approvals()

    assert expired == 0
    persisted = approval_repo.get("tenant_01", "app_sql_01")
    assert persisted is not None
    assert persisted.status == ApprovalStatus.APPROVED


# ==============================================================================
# Feature 41: Worker Payment Reconciliation Backoff & Retry Cap
# ==============================================================================


def test_worker_reconciliation_exponential_backoff() -> None:
    """Verify worker exponentially backs off when payment reconciliation fails or times out."""
    pay_repo = InMemoryPaymentRepository()
    mock_reconciler = MagicMock()
    mock_reconciler.reconcile_timeout.side_effect = RuntimeError(
        "Payment provider 503 gateway timeout"
    )

    worker = BackgroundWorker(
        payment_repo=pay_repo,
        payment_reconciler=mock_reconciler,
        payment_timeout_seconds=60,
        max_reconciliation_retries=5,
        reconciliation_initial_backoff_seconds=2.0,
        reconciliation_max_backoff_seconds=60.0,
    )

    now = datetime.now(UTC)
    attempt = PaymentAttempt(
        attempt_id="att_stuck_01",
        intent_id="int_01",
        tenant_id="tenant_01",
        amount=Money(1000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="key_01",
        provider="mock",
        created_at=now - timedelta(seconds=120),
    )
    pay_repo.save("tenant_01", attempt)

    # Tick 1: Reconciler is called once and fails
    stats1 = worker.run_once()
    assert stats1["reconciled_payments"] == 0
    assert mock_reconciler.reconcile_timeout.call_count == 1
    assert worker._reconciliation_retries["att_stuck_01"] == 1
    assert "att_stuck_01" in worker._reconciliation_next_retry

    # Tick 2 immediately after: Attempt is in backoff window (2s) -> must be skipped
    stats2 = worker.run_once()
    assert stats2["reconciled_payments"] == 0
    assert mock_reconciler.reconcile_timeout.call_count == 1  # Not called again!

    # Simulate elapsed backoff time:
    worker._reconciliation_next_retry["att_stuck_01"] = now - timedelta(seconds=1)

    # Tick 3: Backoff elapsed, reconciler is called a second time
    stats3 = worker.run_once()
    assert stats3["reconciled_payments"] == 0
    assert mock_reconciler.reconcile_timeout.call_count == 2
    assert worker._reconciliation_retries["att_stuck_01"] == 2


def test_worker_reconciliation_max_retries_quarantine() -> None:
    """Verify worker stops retrying when max_reconciliation_retries threshold is reached."""
    pay_repo = InMemoryPaymentRepository()
    mock_reconciler = MagicMock()
    mock_reconciler.reconcile_timeout.side_effect = RuntimeError("Persistent failure")

    worker = BackgroundWorker(
        payment_repo=pay_repo,
        payment_reconciler=mock_reconciler,
        payment_timeout_seconds=60,
        max_reconciliation_retries=3,
        reconciliation_initial_backoff_seconds=1.0,
    )

    now = datetime.now(UTC)
    attempt = PaymentAttempt(
        attempt_id="att_quarantine_01",
        intent_id="int_01",
        tenant_id="tenant_01",
        amount=Money(1000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="key_quarantine",
        provider="mock",
        created_at=now - timedelta(seconds=120),
    )
    pay_repo.save("tenant_01", attempt)

    # Fail 3 times, manually expiring backoff between ticks
    for _ in range(3):
        worker._reconciliation_next_retry["att_quarantine_01"] = now - timedelta(seconds=1)
        worker.run_once()

    assert mock_reconciler.reconcile_timeout.call_count == 3
    assert worker._reconciliation_retries["att_quarantine_01"] == 3

    # Subsequent tick: retry count reached max -> reconciler is NOT called
    worker._reconciliation_next_retry["att_quarantine_01"] = now - timedelta(seconds=1)
    worker.run_once()
    assert mock_reconciler.reconcile_timeout.call_count == 3


def test_worker_reconciliation_cleanup_on_success() -> None:
    """Verify retry tracking and backoff are cleaned up when reconciliation succeeds."""
    pay_repo = InMemoryPaymentRepository()
    mock_reconciler = MagicMock()

    resolved_attempt = PaymentAttempt(
        attempt_id="att_success_01",
        intent_id="int_01",
        tenant_id="tenant_01",
        amount=Money(1000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="key_success",
        provider="mock",
    )
    mock_reconciler.reconcile_timeout.return_value = resolved_attempt

    worker = BackgroundWorker(
        payment_repo=pay_repo,
        payment_reconciler=mock_reconciler,
        payment_timeout_seconds=60,
    )

    now = datetime.now(UTC)
    stuck_attempt = PaymentAttempt(
        attempt_id="att_success_01",
        intent_id="int_01",
        tenant_id="tenant_01",
        amount=Money(1000, "INR"),
        status=PaymentStatus.UNKNOWN,
        idempotency_key="key_success",
        provider="mock",
        created_at=now - timedelta(seconds=120),
    )
    pay_repo.save("tenant_01", stuck_attempt)

    # Pre-populate failure tracking
    worker._reconciliation_retries["att_success_01"] = 2
    worker._reconciliation_next_retry["att_success_01"] = now - timedelta(seconds=1)

    stats = worker.run_once()
    assert stats["reconciled_payments"] == 1
    assert "att_success_01" not in worker._reconciliation_retries
    assert "att_success_01" not in worker._reconciliation_next_retry


# ==============================================================================
# Feature 42: EventBus Memory Leak Mitigation
# ==============================================================================


def test_event_bus_bounded_lru_cache() -> None:
    """Verify EventBus bounds _processed_events to max_processed_events without memory leak."""
    max_cap = 10
    bus = EventBus(max_processed_events=max_cap)
    received: list[str] = []
    bus.subscribe("test.event", lambda ev: received.append(ev.event_id))

    # Publish 25 unique events
    for i in range(25):
        event = OutboxEvent(
            event_id=f"evt_{i:03d}",
            tenant_id="tenant_01",
            event_type="test.event",
            aggregate_type="order",
            aggregate_id=f"ord_{i}",
            payload={"idx": i},
        )
        count = bus.publish(event)
        assert count == 1

    assert len(received) == 25
    # Bounded cache must never exceed max_cap
    assert len(bus._processed_events) == max_cap

    # The oldest events (0..14) should be evicted
    for i in range(15):
        assert f"evt_{i:03d}" not in bus._processed_events

    # The latest events (15..24) must still be present
    for i in range(15, 25):
        assert f"evt_{i:03d}" in bus._processed_events

    # Publishing an active in-cache event again must be dropped as duplicate
    dup_event = OutboxEvent(
        event_id="evt_024",
        tenant_id="tenant_01",
        event_type="test.event",
        aggregate_type="order",
        aggregate_id="ord_24",
        payload={"idx": 24},
    )
    assert bus.publish(dup_event) == 0

    # Clear empties cache
    bus.clear()
    assert len(bus._processed_events) == 0


# ==============================================================================
# Feature 43: Webhook Transient Replay Lockout Fix
# ==============================================================================


def test_in_memory_webhook_claim_transient_error_retry() -> None:
    """Verify claim_event allows re-claiming events whose prior delivery ended in transient failure."""
    repo = InMemoryProviderWebhookEventRepository()

    # 1. Initial claim
    ev, is_new = repo.claim_event(provider="razorpay", event_id="evt_retry_01")
    assert is_new is True
    assert ev.status == WebhookEventStatus.PROCESSING

    # 2. Mark failed with RECONCILIATION_FAILED
    failed = ev.mark_failed("RECONCILIATION_FAILED")
    repo.save(failed)

    # 3. Provider retries delivery -> must be allowed as re-claim (is_new=True)
    retried, is_new2 = repo.claim_event(provider="razorpay", event_id="evt_retry_01")
    assert is_new2 is True
    assert retried.status == WebhookEventStatus.PROCESSING
    assert retried.attempt_count == 2

    # 4. Mark failed with DATABASE_TIMEOUT
    failed2 = retried.mark_failed("DATABASE_TIMEOUT")
    repo.save(failed2)

    # 5. Provider retries delivery again -> allowed
    retried2, is_new3 = repo.claim_event(provider="razorpay", event_id="evt_retry_01")
    assert is_new3 is True
    assert retried2.status == WebhookEventStatus.PROCESSING
    assert retried2.attempt_count == 3

    # 6. Succeeded -> marked PROCESSED
    processed = retried2.mark_processed("pay_123")
    repo.save(processed)

    # 7. Subsequent delivery is now blocked as replay (is_new=False)
    blocked, is_new4 = repo.claim_event(provider="razorpay", event_id="evt_retry_01")
    assert is_new4 is False
    assert blocked.status == WebhookEventStatus.PROCESSED


def test_sqlalchemy_webhook_claim_transient_error_retry() -> None:
    """Verify relational database SqlAlchemyProviderWebhookEventRepository permits transient retries."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    repo = SqlAlchemyProviderWebhookEventRepository(session_factory)

    # 1. Initial claim
    ev, is_new = repo.claim_event(provider="stripe", event_id="evt_sql_retry_01")
    assert is_new is True
    assert ev.status == WebhookEventStatus.PROCESSING

    # 2. Mark failed with RECONCILIATION_FAILED
    failed = ev.mark_failed("RECONCILIATION_FAILED")
    repo.save(failed)

    # 3. Re-claim via database uniqueness conflict recovery
    retried, is_new2 = repo.claim_event(provider="stripe", event_id="evt_sql_retry_01")
    assert is_new2 is True
    assert retried.status == WebhookEventStatus.PROCESSING
    assert retried.attempt_count == 2

    # 4. Mark PROCESSED
    processed = retried.mark_processed("ch_123")
    repo.save(processed)

    # 5. Subsequent delivery is blocked
    blocked, is_new3 = repo.claim_event(provider="stripe", event_id="evt_sql_retry_01")
    assert is_new3 is False
    assert blocked.status == WebhookEventStatus.PROCESSED


def test_api_webhook_reconciliation_transient_retry_e2e() -> None:
    """E2E test: when webhook reconciliation encounters a transient 500 error,

    provider redelivery of the identical webhook payload must be accepted and reprocessed,
    not rejected with 409 WEBHOOK_REPLAY_DETECTED.
    """
    app = create_app()
    client = ApiTestClient(app)

    # Seed an attempt and order in app orchestrator
    attempt = PaymentAttempt(
        attempt_id="att_e2e_reconcile_01",
        intent_id="intent_01",
        tenant_id="tenant_01",
        amount=Money(99900, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="key_e2e_01",
        provider="mock",
    )
    assert app.orchestrator._payment_repo is not None
    app.orchestrator._payment_repo.save("tenant_01", attempt)

    order = Order(
        order_id="ord_e2e_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="user_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=1,
        total_amount=Money(99900, "INR"),
        status=OrderStatus.PENDING_PAYMENT,
        payment_attempt_id="att_e2e_reconcile_01",
    )
    assert app.orchestrator._order_repo is not None
    app.orchestrator._order_repo.save("tenant_01", order)

    payload = {
        "event_id": "evt_e2e_transient_retry_01",
        "event_type": "payment.captured",
        "attempt_id": "att_e2e_reconcile_01",
    }

    # 1. First delivery encounters a simulated transient failure in reconciler
    with patch.object(
        app.reconciler,
        "reconcile_webhook_event",
        side_effect=RuntimeError("Transient database lock timeout"),
    ):
        r1 = client.post_webhook("payment.captured", payload, tenant_id="tenant_01")
        assert r1.status_code == 500
        assert r1.body.get("error", {}).get("code") == "RECONCILIATION_FAILED"

    # 2. Provider redelivers the exact same webhook payload after service recovery
    # Must NOT return 409 WEBHOOK_REPLAY_DETECTED!
    r2 = client.post_webhook("payment.captured", payload, tenant_id="tenant_01")
    assert r2.status_code == 200
    assert r2.body.get("status") == "PROCESSED"

    # 3. Third delivery after success is correctly rejected as 409 replay
    r3 = client.post_webhook("payment.captured", payload, tenant_id="tenant_01")
    assert r3.status_code == 409
    assert r3.body.get("error", {}).get("code") == "WEBHOOK_REPLAY_DETECTED"

"""Comprehensive unit and integration tests for Outbox, EventBus, and BackgroundWorker (§104, §105, §106)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.outbox_repository import InMemoryOutboxRepository
from agentpay.adapters.in_memory.payment_repository import InMemoryPaymentRepository
from agentpay.application.services.event_bus import EventBus
from agentpay.domain.approval import Approval, ApprovalBinding, ApprovalStatus
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.money import Money
from agentpay.domain.outbox import OutboxEvent, OutboxStatus
from agentpay.domain.payments import PaymentAttempt, PaymentStatus
from agentpay.infrastructure.database.base import Base
from agentpay.infrastructure.database.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)
from agentpay.workers.dispatcher import OutboxDispatcher
from agentpay.workers.worker import BackgroundWorker


def test_outbox_event_domain_lifecycle() -> None:
    event = OutboxEvent(
        tenant_id="tenant_01",
        event_type="order.created",
        aggregate_type="order",
        aggregate_id="ord_123",
        payload={"amount": 5000},
        max_retries=2,
    )
    assert str(event.status) == OutboxStatus.PENDING
    assert bool(event.can_retry) is True

    # First failure
    event.record_failure("timeout connecting to broker")
    assert str(event.status) == OutboxStatus.PENDING
    assert int(event.retry_count) == 1
    assert bool(event.can_retry) is True

    # Second failure reaches max_retries
    event.record_failure("timeout connecting to broker again")
    assert str(event.status) == OutboxStatus.FAILED
    assert int(event.retry_count) == 2
    assert bool(event.can_retry) is False

    # Dispatch transition on fresh event
    dispatched_event = OutboxEvent(
        tenant_id="tenant_01",
        event_type="order.created",
        aggregate_type="order",
        aggregate_id="ord_123",
        payload={"amount": 5000},
    )
    dispatched_event.mark_dispatched()
    assert str(dispatched_event.status) == OutboxStatus.DISPATCHED
    assert dispatched_event.dispatched_at is not None
    assert dispatched_event.error_message is None


def test_in_memory_outbox_repository() -> None:
    repo = InMemoryOutboxRepository()
    event1 = OutboxEvent(
        tenant_id="tenant_01",
        event_type="order.created",
        aggregate_type="order",
        aggregate_id="ord_1",
        payload={"sku": "A"},
    )
    event2 = OutboxEvent(
        tenant_id="tenant_02",
        event_type="payment.captured",
        aggregate_type="payment",
        aggregate_id="pay_1",
        payload={"minor": 1000},
    )

    repo.save(event1)
    repo.save(event2)

    assert repo.get("tenant_01", event1.event_id) == event1
    assert repo.get("tenant_02", event1.event_id) is None

    # List pending tenant-scoped vs global
    pending_all = repo.list_pending()
    assert len(pending_all) == 2

    pending_t1 = repo.list_pending("tenant_01")
    assert len(pending_t1) == 1
    assert pending_t1[0].event_id == event1.event_id

    # Mark dispatched
    repo.mark_dispatched("tenant_01", event1.event_id)
    assert repo.get("tenant_01", event1.event_id).status == OutboxStatus.DISPATCHED  # type: ignore[union-attr]
    assert len(repo.list_pending("tenant_01")) == 0


def test_sqlalchemy_outbox_repository() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    repo = SqlAlchemyOutboxRepository(session_factory)

    event = OutboxEvent(
        tenant_id="tenant_rel",
        event_type="agent.registered",
        aggregate_type="agent",
        aggregate_id="agent_99",
        payload={"capabilities": ["search", "purchase"]},
    )

    # Cross-tenant save rejection
    with pytest.raises(CrossTenantAccessError):
        repo.save("tenant_wrong", event)

    # Successful save
    repo.save("tenant_rel", event)

    fetched = repo.get("tenant_rel", event.event_id)
    assert fetched is not None
    assert fetched.event_id == event.event_id
    assert fetched.payload["capabilities"] == ["search", "purchase"]

    pending = repo.list_pending("tenant_rel")
    assert len(pending) == 1

    # Mark dispatched
    repo.mark_dispatched("tenant_rel", event.event_id)
    assert repo.count("tenant_rel", OutboxStatus.DISPATCHED) == 1
    assert repo.count("tenant_rel", OutboxStatus.PENDING) == 0


def test_event_bus_pub_sub_and_wildcard() -> None:
    bus = EventBus()
    received_order: list[dict[str, Any]] = []
    received_all: list[str] = []

    def on_order(evt: OutboxEvent) -> None:
        received_order.append(evt.payload)

    def on_wildcard(evt: OutboxEvent) -> None:
        received_all.append(evt.event_type)

    bus.subscribe("order.placed", on_order)
    bus.subscribe("*", on_wildcard)

    event = OutboxEvent(
        tenant_id="t1",
        event_type="order.placed",
        aggregate_type="order",
        aggregate_id="o1",
        payload={"total": 9900},
    )

    handlers_run = bus.publish(event)
    assert handlers_run == 2
    assert len(received_order) == 1
    assert received_order[0] == {"total": 9900}
    assert received_all == ["order.placed"]

    # Idempotent second delivery returns 0
    assert bus.publish(event, allow_duplicate=False) == 0


def test_event_bus_error_isolation() -> None:
    bus = EventBus()
    order_received = False

    def failing_handler(evt: OutboxEvent) -> None:
        raise ValueError("Simulated handler crash")

    def successful_handler(evt: OutboxEvent) -> None:
        nonlocal order_received
        order_received = True

    bus.subscribe("order.failed", failing_handler)
    bus.subscribe("order.failed", successful_handler)

    event = OutboxEvent(
        tenant_id="t1",
        event_type="order.failed",
        aggregate_type="order",
        aggregate_id="o1",
        payload={"reason": "insufficient_funds"},
    )

    # EventBus isolates execution but raises after all handlers run
    with pytest.raises(RuntimeError) as exc_info:
        bus.publish(event)

    assert "Simulated handler crash" in str(exc_info.value)
    assert order_received is True


def test_outbox_dispatcher_batch_flow() -> None:
    repo = InMemoryOutboxRepository()
    bus = EventBus()
    dispatcher = OutboxDispatcher(repo, bus)

    dispatched_events: list[str] = []
    bus.subscribe("catalog.updated", lambda e: dispatched_events.append(e.aggregate_id))

    event1 = OutboxEvent(
        tenant_id="t1",
        event_type="catalog.updated",
        aggregate_type="product",
        aggregate_id="prod_01",
        payload={},
    )
    event2 = OutboxEvent(
        tenant_id="t1",
        event_type="catalog.updated",
        aggregate_type="product",
        aggregate_id="prod_02",
        payload={},
    )
    repo.save(event1)
    repo.save(event2)

    dispatched, failed = dispatcher.dispatch_batch(limit=10)
    assert dispatched == 2
    assert failed == 0
    assert dispatched_events == ["prod_01", "prod_02"]
    assert len(repo.list_pending("t1")) == 0


def test_background_worker_run_once_integration() -> None:
    outbox_repo = InMemoryOutboxRepository()
    event_bus = EventBus()
    payment_repo = InMemoryPaymentRepository()
    approval_repo = InMemoryApprovalRepository()

    # Seed pending outbox event
    outbox_repo.save(
        OutboxEvent(
            tenant_id="tenant_w",
            event_type="test.event",
            aggregate_type="test",
            aggregate_id="t1",
            payload={"msg": "hello"},
        )
    )

    # Seed stuck UNKNOWN payment from 10 minutes ago
    old_time = datetime.now(UTC) - timedelta(minutes=10)
    stuck_payment = PaymentAttempt(
        attempt_id="att_stuck",
        intent_id="intent_stuck",
        tenant_id="tenant_w",
        amount=Money(5000, "INR"),
        provider="mock_razorpay",
        idempotency_key="idemp_stuck",
        status=PaymentStatus.UNKNOWN,
        created_at=old_time,
    )
    payment_repo.save("tenant_w", stuck_payment)

    # Seed stale PENDING approval from 2 hours ago
    binding = ApprovalBinding(
        action_id="act_app_stale",
        amount=Money(10000, "INR"),
        merchant_id="merch_1",
        resource_id="prod_1",
        currency="INR",
        action_version=1,
        tenant_id="tenant_w",
    )
    stale_approval = Approval(
        approval_id="app_stale",
        tenant_id="tenant_w",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=datetime.now(UTC) - timedelta(hours=2),
    )
    approval_repo.save("tenant_w", stale_approval)

    # Mock payment reconciler
    reconciler = MagicMock()

    worker = BackgroundWorker(
        outbox_repo=outbox_repo,
        event_bus=event_bus,
        payment_repo=payment_repo,
        payment_reconciler=reconciler,
        approval_repo=approval_repo,
        approval_ttl_seconds=3600,
        payment_timeout_seconds=300,
    )

    # Execute single tick
    stats = worker.run_once()
    assert stats["outbox_dispatched"] == 1
    assert stats["outbox_failed"] == 0
    assert stats["reconciled_payments"] == 1
    assert stats["expired_approvals"] == 1

    # Verify reconciler called
    reconciler.reconcile_timeout.assert_called_once_with(stuck_payment)

    # Verify approval transitioned to REJECTED (expired past TTL)
    updated_app = approval_repo.get("tenant_w", "app_stale")
    assert updated_app is not None
    assert updated_app.status == ApprovalStatus.REJECTED
    assert "expired" in (updated_app.reason or "").lower()


def test_standalone_agentpay_worker_runtime() -> None:
    """Verifies apps/worker/src/agentpay_worker/worker.py BackgroundWorker runs cleanly on frozen Approval and stuck Payment (§104)."""
    import sys
    from pathlib import Path

    worker_src = str(Path(__file__).resolve().parents[4] / "apps" / "worker" / "src")
    if worker_src not in sys.path:
        sys.path.insert(0, worker_src)

    from agentpay_worker.worker import (
        BackgroundWorker as StandaloneWorker,
    )

    outbox_repo = InMemoryOutboxRepository()
    event_bus = EventBus()
    payment_repo = InMemoryPaymentRepository()
    approval_repo = InMemoryApprovalRepository()

    # Seed stuck UNKNOWN payment
    stuck_payment = PaymentAttempt(
        attempt_id="att_standalone_stuck",
        intent_id="intent_standalone",
        tenant_id="tenant_s",
        amount=Money(3000, "INR"),
        provider="mock_razorpay",
        idempotency_key="idemp_s",
        status=PaymentStatus.UNKNOWN,
        created_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    payment_repo.save("tenant_s", stuck_payment)

    # Seed stale PENDING approval (frozen dataclass)
    binding = ApprovalBinding(
        action_id="act_standalone_stale",
        amount=Money(5000, "INR"),
        merchant_id="m1",
        resource_id="r1",
        currency="INR",
        action_version=1,
        tenant_id="tenant_s",
    )
    stale_app = Approval(
        approval_id="app_standalone_stale",
        tenant_id="tenant_s",
        binding=binding,
        status=ApprovalStatus.PENDING,
        requested_at=datetime.now(UTC) - timedelta(hours=2),
    )
    approval_repo.save("tenant_s", stale_app)

    reconciler = MagicMock()

    worker = StandaloneWorker(
        outbox_repo=outbox_repo,
        event_bus=event_bus,
        payment_repo=payment_repo,
        payment_reconciler=reconciler,
        approval_repo=approval_repo,
        approval_ttl_seconds=3600,
        payment_timeout_seconds=300,
    )

    stats = worker.run_once()
    assert stats["reconciled_payments"] == 1
    assert stats["expired_approvals"] == 1

    reconciler.reconcile_timeout.assert_called_once_with(stuck_payment)

    updated_app = approval_repo.get("tenant_s", "app_standalone_stale")
    assert updated_app is not None
    assert updated_app.status == ApprovalStatus.REJECTED
    assert "expired" in (updated_app.reason or "").lower()

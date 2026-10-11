"""Unit and property tests for Payments, Orders, Idempotency, and Saga components."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.adapters.in_memory.payment_repository import (
    InMemoryIdempotencyStore,
    InMemoryOrderRepository,
    InMemoryPaymentRepository,
)
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.domain.errors import (
    CrossTenantAccessError,
    InvalidPaymentError,
)
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)


def test_payment_attempt_and_transitions() -> None:
    attempt = PaymentAttempt(
        attempt_id="pay_01",
        intent_id="intent_01",
        tenant_id="tenant_01",
        amount=Money(5000, "INR"),
        status=PaymentStatus.CREATED,
        idempotency_key="idem_01",
        provider="mock",
    )
    assert attempt.is_successful is False

    captured = attempt.transition_to(PaymentStatus.CAPTURED, provider_payment_id="rzp_123")
    assert captured.is_successful is True
    assert captured.status == PaymentStatus.CAPTURED
    assert captured.provider_payment_id == "rzp_123"

    with pytest.raises(InvalidPaymentError, match="attempt_id"):
        PaymentAttempt(
            attempt_id="",
            intent_id="i1",
            tenant_id="t1",
            amount=Money(10, "INR"),
            status=PaymentStatus.CREATED,
            idempotency_key="k",
            provider="p",
        )


def test_order_lifecycle() -> None:
    order = Order(
        order_id="ord_01",
        tenant_id="tenant_01",
        merchant_id="merch_01",
        buyer_id="buyer_01",
        agent_id="agent_01",
        variant_id="var_01",
        quantity=2,
        total_amount=Money(10000, "INR"),
    )
    assert order.status == OrderStatus.PENDING_PAYMENT

    confirmed = order.confirm(payment_attempt_id="pay_01")
    assert confirmed.status == OrderStatus.CONFIRMED
    assert confirmed.payment_attempt_id == "pay_01"

    cancelled = order.cancel(reason="Buyer requested")
    assert cancelled.status == OrderStatus.CANCELLED


def test_mock_payment_provider_success() -> None:
    provider = MockPaymentProvider()
    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(1500, "INR"),
        idempotency_key="idem_key_01",
    )
    assert attempt.status == PaymentStatus.CAPTURED
    assert attempt.is_successful is True
    assert attempt.provider_payment_id is not None


def test_mock_payment_provider_decline() -> None:
    provider = MockPaymentProvider(simulate_decline=True)
    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(1500, "INR"),
        idempotency_key="idem_key_02",
    )
    assert attempt.status == PaymentStatus.DECLINED
    assert attempt.failure_reason is not None


def test_mock_payment_provider_timeout_and_reconciliation() -> None:
    # 1. Payment initiates and times out yielding UNKNOWN (§43)
    provider = MockPaymentProvider(simulate_timeout=True)
    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(5000, "INR"),
        idempotency_key="idem_timeout_01",
    )
    assert attempt.status == PaymentStatus.UNKNOWN
    assert attempt.failure_reason == "Provider gateway timeout"

    # 2. Never blindly retry; query provider or reconcile (§43, §112)
    reconciled = provider.reconcile_timeout("tenant_01", attempt.attempt_id)
    assert reconciled.status == PaymentStatus.CAPTURED
    assert reconciled.is_successful is True


def test_mock_payment_provider_idempotency_replay() -> None:
    provider = MockPaymentProvider()
    # First call
    attempt1 = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(5000, "INR"),
        idempotency_key="unique_key_100",
    )
    # Replay identical request
    attempt2 = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(5000, "INR"),
        idempotency_key="unique_key_100",
    )
    assert attempt1.attempt_id == attempt2.attempt_id
    assert attempt1.provider_payment_id == attempt2.provider_payment_id


def test_mock_payment_provider_refund() -> None:
    provider = MockPaymentProvider()
    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(5000, "INR"),
        idempotency_key="idem_refund_01",
    )
    refund = provider.refund(
        tenant_id="tenant_01",
        attempt_id=attempt.attempt_id,
        amount=Money(5000, "INR"),
        reason="Order cancellation",
    )
    assert refund.amount.minor_units == 5000
    updated_attempt = provider.get_payment("tenant_01", attempt.attempt_id)
    assert updated_attempt.status == PaymentStatus.REFUNDED


def test_in_memory_payment_repositories() -> None:
    pay_repo = InMemoryPaymentRepository()
    order_repo = InMemoryOrderRepository()
    idem_store = InMemoryIdempotencyStore()

    t1 = "tenant_01"
    t2 = "tenant_02"

    attempt = PaymentAttempt(
        attempt_id="p1",
        intent_id="i1",
        tenant_id=t1,
        amount=Money(100, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="k1",
        provider="mock",
    )
    pay_repo.save(t1, attempt)
    assert pay_repo.get(t1, "p1") == attempt
    assert pay_repo.get(t2, "p1") is None

    with pytest.raises(CrossTenantAccessError):
        pay_repo.save(t2, attempt)

    order = Order(
        order_id="o1",
        tenant_id=t1,
        merchant_id="m1",
        buyer_id="b1",
        agent_id="a1",
        variant_id="v1",
        quantity=1,
        total_amount=Money(100, "INR"),
    )
    order_repo.save(t1, order)
    assert order_repo.get(t1, "o1") == order
    assert order_repo.get(t2, "o1") is None

    idem_rec = IdempotencyRecord(
        key="idem_k1",
        tenant_id=t1,
        operation_type="payment",
        request_hash="hash123",
        result_data={"status": "success"},
    )
    idem_store.save(t1, idem_rec)
    assert idem_store.get(t1, "idem_k1") == idem_rec
    assert idem_store.get(t2, "idem_k1") is None


@given(st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz0123456789"))
def test_hypothesis_idempotency_key_replay_uniqueness(key_suffix: str) -> None:
    provider = MockPaymentProvider()
    key = f"key_{key_suffix}"
    attempt_a = provider.create_payment("t1", "intent_1", Money(100, "INR"), key)
    attempt_b = provider.create_payment("t1", "intent_1", Money(100, "INR"), key)
    assert attempt_a.attempt_id == attempt_b.attempt_id


def test_payment_attempt_state_machine_transition_table_and_illegal_pairs() -> None:
    """Verifies B-PAY-01: PaymentAttempt enforces valid state machine transitions (§41, §125)."""
    # 1. Idempotent self-transitions are always valid for all states
    for status in PaymentStatus:
        att = PaymentAttempt(
            attempt_id="att_self",
            intent_id="i_self",
            tenant_id="tenant_01",
            amount=Money(5000, "INR"),
            status=status,
            idempotency_key=f"idem_{status.value}",
            provider="mock",
        )
        self_trans = att.transition_to(status)
        assert self_trans.status == status

    # 2. Terminal states cannot transition to anything
    for terminal_status in (PaymentStatus.FAILED, PaymentStatus.DECLINED, PaymentStatus.REFUNDED):
        terminal_att = PaymentAttempt(
            attempt_id="att_term",
            intent_id="i_term",
            tenant_id="tenant_01",
            amount=Money(5000, "INR"),
            status=terminal_status,
            idempotency_key=f"idem_{terminal_status.value}",
            provider="mock",
        )
        for target_status in PaymentStatus:
            if target_status == terminal_status:
                continue
            assert terminal_att.can_transition_to(target_status) is False
            with pytest.raises(InvalidPaymentError, match="Illegal payment transition"):
                terminal_att.transition_to(target_status)

    # 3. CAPTURED cannot transition to non-refund states
    captured_att = PaymentAttempt(
        attempt_id="att_cap",
        intent_id="i_cap",
        tenant_id="tenant_01",
        amount=Money(5000, "INR"),
        status=PaymentStatus.CAPTURED,
        idempotency_key="idem_cap",
        provider="mock",
    )
    for invalid_target in (
        PaymentStatus.CREATED,
        PaymentStatus.AUTHORIZED,
        PaymentStatus.PENDING,
        PaymentStatus.FAILED,
        PaymentStatus.DECLINED,
        PaymentStatus.UNKNOWN,
    ):
        assert captured_att.can_transition_to(invalid_target) is False
        with pytest.raises(InvalidPaymentError, match="Illegal payment transition"):
            captured_att.transition_to(invalid_target)

    # 4. CAPTURED can transition to REFUNDED and PARTIALLY_REFUNDED
    assert captured_att.can_transition_to(PaymentStatus.REFUNDED) is True
    assert captured_att.can_transition_to(PaymentStatus.PARTIALLY_REFUNDED) is True


def test_provider_capture_payment_requires_authorized_state() -> None:
    """Verifies B-PAY-01: capture_payment across all providers rejects non-authorizable states."""
    from agentpay.adapters.razorpay_payment_provider import RazorpayPaymentProvider
    from agentpay.adapters.stripe_payment_provider import StripePaymentProvider

    providers: list[Any] = [
        MockPaymentProvider(auto_capture=False),
        RazorpayPaymentProvider(auto_capture=False),
        StripePaymentProvider(auto_capture=False),
    ]

    for provider in providers:
        # Create attempt in AUTHORIZED state
        att = provider.create_payment(
            "t1", "i1", Money(1000, "INR"), f"idem_{provider.__class__.__name__}"
        )
        assert att.status == PaymentStatus.AUTHORIZED

        # Capture authorized attempt succeeds
        cap = provider.capture_payment("t1", att.attempt_id)
        assert cap.status == PaymentStatus.CAPTURED

        # Idempotent capture of already-captured attempt succeeds
        cap_idemp = provider.capture_payment("t1", att.attempt_id)
        assert cap_idemp.status == PaymentStatus.CAPTURED

        # Attempts in terminal or non-authorizable states must be rejected by capture_payment
        for non_auth_status in (
            PaymentStatus.FAILED,
            PaymentStatus.DECLINED,
            PaymentStatus.REFUNDED,
            PaymentStatus.UNKNOWN,
        ):
            bad_att_id = f"bad_{non_auth_status.value}_{provider.__class__.__name__}"
            # Directly seed non-authorizable attempt into provider internal dict
            bad_att = PaymentAttempt(
                attempt_id=bad_att_id,
                intent_id="i_bad",
                tenant_id="t1",
                amount=Money(1000, "INR"),
                status=non_auth_status,
                idempotency_key=f"idem_{bad_att_id}",
                provider="test",
            )
            provider._attempts[("t1", bad_att_id)] = bad_att

            with pytest.raises(InvalidPaymentError, match="non-authorizable status"):
                provider.capture_payment("t1", bad_att_id)

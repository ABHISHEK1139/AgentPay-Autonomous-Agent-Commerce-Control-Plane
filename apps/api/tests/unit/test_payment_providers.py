"""Unit tests for Stripe and Razorpay production payment provider adapters (§39, §41, §42, §43, §61).

Tests:
- Payment creation with integer minor unit precision and idempotency keys (§39, §42).
- Capture and refund lifecycle transitions (§41, §44).
- HMAC-SHA256 signature verification and timing-safe comparison (§61).
- Timestamp tolerance and anti-replay defense (§61).
- Inbound webhook parsing and integration with PaymentReconciler (§43, §61).
"""

from __future__ import annotations

import json
import time

import pytest

from agentpay.adapters.in_memory.payment_repository import InMemoryPaymentRepository
from agentpay.adapters.razorpay_payment_provider import (
    RazorpayPaymentProvider,
    RazorpayWebhookSignatureError,
)
from agentpay.adapters.stripe_payment_provider import (
    StripePaymentProvider,
    StripeWebhookSignatureError,
)
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.money import Money
from agentpay.domain.payments import PaymentStatus


def test_stripe_payment_lifecycle_and_idempotency() -> None:
    """Stripe provider manages payment lifecycle with strict idempotency replay (§39, §42)."""
    provider = StripePaymentProvider(auto_capture=False)
    tenant_id = "tenant_01"
    amount = Money(250000, "INR")

    # 1. Create payment (AUTHORIZED)
    attempt = provider.create_payment(
        tenant_id=tenant_id,
        intent_id="intent_01",
        amount=amount,
        idempotency_key="idem_stripe_01",
    )
    assert attempt.provider == "stripe"
    assert attempt.status == PaymentStatus.AUTHORIZED
    assert attempt.amount == amount
    assert attempt.provider_payment_id is not None
    assert attempt.provider_payment_id.startswith("pi_stripe_")

    # 2. Idempotency replay returns exact same attempt
    replayed = provider.create_payment(
        tenant_id=tenant_id,
        intent_id="intent_01",
        amount=amount,
        idempotency_key="idem_stripe_01",
    )
    assert replayed.attempt_id == attempt.attempt_id
    assert replayed.provider_payment_id == attempt.provider_payment_id

    # 3. Capture payment
    captured = provider.capture_payment(tenant_id, attempt.attempt_id)
    assert captured.status == PaymentStatus.CAPTURED

    # 4. Refund payment
    refund = provider.refund(
        tenant_id=tenant_id,
        attempt_id=attempt.attempt_id,
        amount=amount,
        reason="Customer request",
    )
    assert refund.refund_id.startswith("re_stripe_")
    assert refund.payment_attempt_id == attempt.attempt_id
    assert refund.amount == amount

    # Verify attempt status is now REFUNDED
    refreshed = provider.get_payment(tenant_id, attempt.attempt_id)
    assert refreshed.status == PaymentStatus.REFUNDED


def test_stripe_webhook_signature_verification_and_anti_replay() -> None:
    """Stripe webhook verification enforces HMAC-SHA256 and timestamp replay limits (§61)."""
    secret = "whsec_super_secret_stripe_signing_key_42"
    provider = StripePaymentProvider(webhook_secret=secret)

    payload = json.dumps({"id": "evt_test_123", "type": "payment_intent.succeeded"}).encode("utf-8")
    now = int(time.time())

    # 1. Valid signature succeeds
    sig_header = provider.generate_webhook_signature(payload, timestamp=now)
    assert provider.verify_webhook_signature(payload, sig_header, current_time=now) is True

    # 2. Tampered payload fails
    tampered = payload + b" "
    assert provider.verify_webhook_signature(tampered, sig_header, current_time=now) is False

    # 3. Expired timestamp (> 300s) rejected (replay defense)
    old_sig = provider.generate_webhook_signature(payload, timestamp=now - 305)
    assert (
        provider.verify_webhook_signature(payload, old_sig, tolerance_seconds=300, current_time=now)
        is False
    )

    # 4. Future timestamp drift outside tolerance rejected
    future_sig = provider.generate_webhook_signature(payload, timestamp=now + 305)
    assert (
        provider.verify_webhook_signature(
            payload, future_sig, tolerance_seconds=300, current_time=now
        )
        is False
    )

    # 5. Invalid secret fails
    wrong_sig = provider.generate_webhook_signature(
        payload, timestamp=now, secret="whsec_wrong_key"
    )
    assert provider.verify_webhook_signature(payload, wrong_sig, current_time=now) is False


def test_stripe_webhook_parsing_and_reconciliation() -> None:
    """Stripe webhook payloads normalize to canonical events and reconcile attempts (§43, §61)."""
    secret = "whsec_stripe_test_reconciler_secret"
    provider = StripePaymentProvider(webhook_secret=secret)
    repo = InMemoryPaymentRepository()
    reconciler = PaymentReconciler(payment_provider=provider, payment_repo=repo)

    tenant_id = "tenant_01"
    # Seed payment in UNKNOWN state
    attempt = provider.create_payment(tenant_id, "intent_01", Money(50000, "INR"), "idem_rec_01")
    unknown_attempt = attempt.transition_to(PaymentStatus.UNKNOWN)
    repo.save(tenant_id, unknown_attempt)

    # Inbound Stripe webhook payload
    raw_event = {
        "id": "evt_stripe_succeeded",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": attempt.provider_payment_id,
                "amount": 50000,
                "currency": "inr",
                "status": "succeeded",
                "metadata": {
                    "attempt_id": attempt.attempt_id,
                    "tenant_id": tenant_id,
                },
            }
        },
    }
    payload_bytes = json.dumps(raw_event).encode("utf-8")
    sig_header = provider.generate_webhook_signature(payload_bytes)

    # Parse event
    parsed = provider.parse_webhook_event(payload_bytes, sig_header)
    assert parsed["event_type"] == "payment.captured"
    assert parsed["attempt_id"] == attempt.attempt_id

    # Reconcile via PaymentReconciler
    rec_res = reconciler.reconcile_webhook_event(
        tenant_id=tenant_id,
        event_type=parsed["event_type"],
        payload=parsed,
    )
    assert rec_res["status"] == "PROCESSED"
    assert rec_res["payment_status"] == PaymentStatus.CAPTURED.value

    # Verify attempt in repo is CAPTURED
    saved = repo.get(tenant_id, attempt.attempt_id)
    assert saved is not None
    assert saved.status == PaymentStatus.CAPTURED

    # Invalid signature raises StripeWebhookSignatureError
    with pytest.raises(StripeWebhookSignatureError):
        provider.parse_webhook_event(payload_bytes, "t=1234567,v1=bad_signature")


def test_razorpay_payment_lifecycle_and_idempotency() -> None:
    """Razorpay provider manages payment lifecycle with strict idempotency replay (§39, §42)."""
    provider = RazorpayPaymentProvider(auto_capture=False)
    tenant_id = "tenant_01"
    amount = Money(199900, "INR")

    # 1. Create payment (AUTHORIZED)
    attempt = provider.create_payment(
        tenant_id=tenant_id,
        intent_id="intent_rzp_01",
        amount=amount,
        idempotency_key="idem_rzp_01",
    )
    assert attempt.provider == "razorpay"
    assert attempt.status == PaymentStatus.AUTHORIZED
    assert attempt.amount == amount
    assert attempt.provider_payment_id is not None
    assert attempt.provider_payment_id.startswith("pay_rzp_")

    # 2. Idempotency replay
    replayed = provider.create_payment(
        tenant_id=tenant_id,
        intent_id="intent_rzp_01",
        amount=amount,
        idempotency_key="idem_rzp_01",
    )
    assert replayed.attempt_id == attempt.attempt_id

    # 3. Capture payment
    captured = provider.capture_payment(tenant_id, attempt.attempt_id)
    assert captured.status == PaymentStatus.CAPTURED

    # 4. Refund payment
    refund = provider.refund(
        tenant_id=tenant_id,
        attempt_id=attempt.attempt_id,
        amount=amount,
        reason="Order cancellation",
    )
    assert refund.refund_id.startswith("rfnd_rzp_")
    assert refund.payment_attempt_id == attempt.attempt_id
    assert refund.amount == amount

    refreshed = provider.get_payment(tenant_id, attempt.attempt_id)
    assert refreshed.status == PaymentStatus.REFUNDED


def test_razorpay_webhook_signature_verification_and_parsing() -> None:
    """Razorpay X-Razorpay-Signature verification and event parsing (§61)."""
    secret = "whsec_test_rzp_signature_key_99"
    provider = RazorpayPaymentProvider(webhook_secret=secret)

    raw_event = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_rzp_live_12345",
                    "amount": 199900,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {
                        "attempt_id": "pay_attempt_rzp_99",
                        "tenant_id": "tenant_01",
                    },
                }
            }
        },
    }
    payload_bytes = json.dumps(raw_event).encode("utf-8")

    # 1. Valid signature
    sig = provider.generate_webhook_signature(payload_bytes)
    assert provider.verify_webhook_signature(payload_bytes, sig) is True

    # 2. Tampered signature or payload fails
    assert provider.verify_webhook_signature(payload_bytes + b"!", sig) is False
    assert provider.verify_webhook_signature(payload_bytes, "bad_signature_hex") is False

    # 3. Parse valid event
    parsed = provider.parse_webhook_event(payload_bytes, sig)
    assert parsed["event_type"] == "payment.captured"
    assert parsed["attempt_id"] == "pay_attempt_rzp_99"
    assert parsed["tenant_id"] == "tenant_01"

    # 4. Invalid signature raises RazorpayWebhookSignatureError
    with pytest.raises(RazorpayWebhookSignatureError):
        provider.parse_webhook_event(payload_bytes, "invalid_sig_hex")

    # 5. Timestamp tolerance checking (§61)
    now = int(time.time())
    assert (
        provider.verify_webhook_signature(
            payload_bytes, sig, timestamp=now, tolerance_seconds=300, current_time=now
        )
        is True
    )
    # Expired timestamp (> 300s) rejected
    assert (
        provider.verify_webhook_signature(
            payload_bytes, sig, timestamp=now - 305, tolerance_seconds=300, current_time=now
        )
        is False
    )
    # Parse with expired timestamp raises RazorpayWebhookSignatureError
    with pytest.raises(RazorpayWebhookSignatureError):
        provider.parse_webhook_event(
            payload_bytes, sig, timestamp=now - 305, tolerance_seconds=300, current_time=now
        )
    # Parse with valid timestamp succeeds
    parsed_with_ts = provider.parse_webhook_event(
        payload_bytes, sig, timestamp=now, tolerance_seconds=300, current_time=now
    )
    assert parsed_with_ts["event_type"] == "payment.captured"

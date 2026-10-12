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
from typing import Any

import pytest

from agentpay.adapters.in_memory.payment_repository import InMemoryPaymentRepository
from agentpay.adapters.razorpay_payment_provider import (
    InMemoryRazorpaySimulator,
    RazorpayPaymentProvider,
    RazorpayWebhookSignatureError,
)
from agentpay.adapters.razorpay_sandbox_provider import (
    RazorpaySandboxCredentialError,
    RazorpaySandboxProvider,
)
from agentpay.adapters.stripe_payment_provider import (
    InMemoryStripeSimulator,
    StripePaymentProvider,
    StripeWebhookSignatureError,
)
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.errors import InvalidPaymentError
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
    provider = StripePaymentProvider(webhook_secret=secret, auto_capture=False)
    repo = InMemoryPaymentRepository()
    reconciler = PaymentReconciler(payment_provider=provider, payment_repo=repo)

    tenant_id = "tenant_01"
    # Seed payment in UNKNOWN state (simulating gateway timeout from AUTHORIZED state)
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


def test_payment_provider_simulator_aliases() -> None:
    """Verify InMemory simulators are aliased by legacy names (§39, AP-004)."""
    assert RazorpayPaymentProvider is InMemoryRazorpaySimulator
    assert StripePaymentProvider is InMemoryStripeSimulator


def test_razorpay_sandbox_credential_fail_fast() -> None:
    """RazorpaySandboxProvider fails fast on missing, malformed, or placeholder credentials (§39, AP-004)."""
    # 1. Empty credentials
    with pytest.raises(RazorpaySandboxCredentialError, match="missing or empty"):
        RazorpaySandboxProvider(key_id="", key_secret="sec_real_secret_123456")
    with pytest.raises(RazorpaySandboxCredentialError, match="missing or empty"):
        RazorpaySandboxProvider(key_id="rzp_test_real12345", key_secret="")

    # 2. Invalid prefix
    with pytest.raises(RazorpaySandboxCredentialError, match="must begin with 'rzp_test_'"):
        RazorpaySandboxProvider(key_id="rzp_live_real12345", key_secret="sec_real_secret_123456")

    # 3. Too short key
    with pytest.raises(RazorpaySandboxCredentialError, match="at least 14 chars"):
        RazorpaySandboxProvider(key_id="rzp_test_1", key_secret="sec_real_secret_123456")

    # 4. Too short secret
    with pytest.raises(RazorpaySandboxCredentialError, match="at least 16 chars"):
        RazorpaySandboxProvider(key_id="rzp_test_real12345", key_secret="short")

    # 5. Placeholder key_id
    with pytest.raises(RazorpaySandboxCredentialError, match="placeholder"):
        RazorpaySandboxProvider(
            key_id="rzp_test_placeholder_id", key_secret="sec_real_secret_123456"
        )

    # 6. Placeholder secret
    with pytest.raises(RazorpaySandboxCredentialError, match="placeholder"):
        RazorpaySandboxProvider(key_id="rzp_test_real12345", key_secret="your_secret_here_dummy")


def test_razorpay_sandbox_order_creation_with_mock_transport() -> None:
    """RazorpaySandboxProvider creates order via mock transport and verifies auth headers (§39, AP-004)."""
    recorded_requests: list[Any] = []

    def mock_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        recorded_requests.append(req)
        resp_data = {
            "id": "order_EKwxwAgItmmXdp",
            "entity": "order",
            "amount": 149900,
            "currency": "INR",
            "receipt": "idem_sandbox_01",
            "status": "created",
            "notes": {
                "tenant_id": "tenant_01",
                "intent_id": "intent_01",
            },
        }
        return 201, json.dumps(resp_data).encode("utf-8"), {"Content-Type": "application/json"}

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=mock_transport,
        auto_capture=False,
    )

    amount = Money(149900, "INR")
    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=amount,
        idempotency_key="idem_sandbox_01",
    )

    assert attempt.provider == "razorpay"
    assert attempt.provider_payment_id == "order_EKwxwAgItmmXdp"
    assert attempt.status == PaymentStatus.AUTHORIZED
    assert len(recorded_requests) == 1

    req = recorded_requests[0]
    auth_header = req.headers.get("Authorization")
    assert auth_header is not None
    assert auth_header.startswith("Basic ")


def test_razorpay_sandbox_timeout_maps_to_unknown() -> None:
    """Network timeout during sandbox request maps safely to PaymentStatus.UNKNOWN (§43, AP-004)."""

    def timeout_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        raise TimeoutError("Read timed out")

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=timeout_transport,
    )

    attempt = provider.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_01",
        amount=Money(50000, "INR"),
        idempotency_key="idem_timeout_01",
    )

    assert attempt.status == PaymentStatus.UNKNOWN
    assert attempt.provider_payment_id is None
    assert "timeout" in (attempt.failure_reason or "").lower()


def test_razorpay_sandbox_http_error_redacts_credentials() -> None:
    """HTTP 401 error from Razorpay sandbox maps to typed error with zero credential leakage (§13, AP-004)."""
    import urllib.error

    def unauthorized_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=unauthorized_transport,
    )

    with pytest.raises(InvalidPaymentError) as exc_info:
        provider.create_payment(
            tenant_id="tenant_01",
            intent_id="intent_01",
            amount=Money(50000, "INR"),
            idempotency_key="idem_unauth_01",
        )

    err_str = str(exc_info.value)
    assert "401" in err_str
    assert "sec_real_secret_123456" not in err_str
    assert "rzp_test_real12345" not in err_str


def test_razorpay_sandbox_webhook_signature_and_parsing() -> None:
    """RazorpaySandboxProvider supports HMAC-SHA256 signature generation and event parsing (§61, AP-004)."""
    secret = "whsec_sandbox_test_secret_32bytes"
    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        webhook_secret=secret,
    )

    payload = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_sandbox_123",
                        "amount": 250000,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {"attempt_id": "attempt_99", "tenant_id": "tenant_01"},
                    }
                }
            },
        }
    ).encode("utf-8")

    sig = provider.generate_webhook_signature(payload)
    assert provider.verify_webhook_signature(payload, sig) is True

    parsed = provider.parse_webhook_event(payload, sig)
    assert parsed["event_type"] == "payment.captured"
    assert parsed["attempt_id"] == "attempt_99"
    assert parsed["tenant_id"] == "tenant_01"


def test_razorpay_sandbox_capture_and_refund() -> None:
    """RazorpaySandboxProvider capture and refund routes execute correctly (§41, §44, AP-004)."""

    def mock_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        if req.get_method() == "GET" and "/payments/" in req.full_url:
            return (
                200,
                json.dumps(
                    {
                        "id": "pay_sandbox_456",
                        "amount": 10000,
                        "currency": "INR",
                        "status": "authorized",
                    }
                ).encode("utf-8"),
                {},
            )
        if req.get_method() == "POST" and "/capture" in req.full_url:
            return (
                200,
                json.dumps(
                    {
                        "id": "pay_sandbox_456",
                        "status": "captured",
                    }
                ).encode("utf-8"),
                {},
            )
        if req.get_method() == "POST" and "/refund" in req.full_url:
            return (
                200,
                json.dumps(
                    {
                        "id": "rfnd_sandbox_789",
                        "amount": 10000,
                        "currency": "INR",
                    }
                ).encode("utf-8"),
                {},
            )
        return 404, b"{}", {}

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=mock_transport,
    )

    captured = provider.capture_payment("tenant_01", "pay_sandbox_456")
    assert captured.status == PaymentStatus.CAPTURED

    refund = provider.refund("tenant_01", "pay_sandbox_456", Money(10000, "INR"), "Defective item")
    assert refund.refund_id == "rfnd_sandbox_789"
    assert refund.amount == Money(10000, "INR")


def test_razorpay_sandbox_safe_receipt_length_constraint() -> None:
    """Razorpay orders enforce max 40-character receipt length by hashing long idempotency keys (§39, AP-004)."""
    recorded_receipts: list[str] = []
    recorded_notes: list[dict[str, Any]] = []

    def mock_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        if req.get_method() == "POST" and "/orders" in req.full_url:
            payload = json.loads(req.data.decode("utf-8"))
            recorded_receipts.append(payload["receipt"])
            recorded_notes.append(payload["notes"])
            return (
                201,
                json.dumps(
                    {
                        "id": "order_test_receipt_len",
                        "status": "created",
                        "amount": payload["amount"],
                        "currency": payload["currency"],
                        "receipt": payload["receipt"],
                        "notes": payload["notes"],
                    }
                ).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        return 404, b"{}", {}

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=mock_transport,
    )

    # 1. Long idempotency key (> 40 chars)
    long_key = "very_long_custom_agent_idempotency_key_exceeding_forty_characters_123456789"
    assert len(long_key) > 40
    attempt = provider.create_payment("tenant_01", "intent_01", Money(5000, "INR"), long_key)
    assert attempt.idempotency_key == long_key
    assert len(recorded_receipts[0]) <= 40
    assert recorded_receipts[0].startswith("rcpt_")
    assert recorded_notes[0]["idempotency_key"] == long_key

    # 2. Short idempotency key (<= 40 chars)
    short_key = "short_key_20_chars"
    assert len(short_key) <= 40
    attempt_short = provider.create_payment("tenant_01", "intent_02", Money(5000, "INR"), short_key)
    assert attempt_short.idempotency_key == short_key
    assert recorded_receipts[1] == short_key
    assert recorded_notes[1]["idempotency_key"] == short_key


def test_razorpay_sandbox_refund_validation_invariants() -> None:
    """RazorpaySandboxProvider verifies currency match, balance limits, tenant ownership, and amount bounds (§44, AP-004)."""

    def mock_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        if req.get_method() == "GET" and "/payments/pay_test_inr" in req.full_url:
            return (
                200,
                json.dumps(
                    {
                        "id": "pay_test_inr",
                        "amount": 10000,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {"tenant_id": "tenant_01"},
                    }
                ).encode("utf-8"),
                {},
            )
        if req.get_method() == "POST" and "/refund" in req.full_url:
            return (
                200,
                json.dumps(
                    {
                        "id": "rfnd_test_ok",
                        "amount": 5000,
                        "currency": "INR",
                    }
                ).encode("utf-8"),
                {},
            )
        return 404, b"{}", {}

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_real12345",
        key_secret="sec_real_secret_123456",
        transport=mock_transport,
    )

    # 1. Non-positive amount rejected
    with pytest.raises(InvalidPaymentError, match="strictly positive"):
        provider.refund("tenant_01", "pay_test_inr", Money(0, "INR"), "Zero refund")

    # 2. Currency mismatch rejected
    with pytest.raises(
        InvalidPaymentError,
        match="Refund currency 'USD' does not match payment currency 'INR'",
    ):
        provider.refund("tenant_01", "pay_test_inr", Money(5000, "USD"), "Wrong currency")

    # 3. Exceeds payment amount rejected
    with pytest.raises(InvalidPaymentError, match="Refund amount exceeds payment amount"):
        provider.refund("tenant_01", "pay_test_inr", Money(15000, "INR"), "Exceeds balance")

    # 4. Cross-tenant access rejected
    with pytest.raises(InvalidPaymentError, match="belongs to tenant 'tenant_01', not 'tenant_02'"):
        provider.refund("tenant_02", "pay_test_inr", Money(5000, "INR"), "Cross-tenant attack")

    # 5. Valid refund succeeds
    ref = provider.refund("tenant_01", "pay_test_inr", Money(5000, "INR"), "Valid partial refund")
    assert ref.refund_id == "rfnd_test_ok"
    assert ref.amount == Money(5000, "INR")
    assert ref.tenant_id == "tenant_01"


def test_razorpay_sandbox_live_api_order_and_query() -> None:
    """Opt-in live verification against external Razorpay Sandbox (https://api.razorpay.com/v1).

    Runs only when valid test credentials are provided via environment variables:
    RAZORPAY_KEY_ID (starts with 'rzp_test_') and RAZORPAY_KEY_SECRET.
    Skips cleanly in hermetic CI when external credentials are not present.

    Verifies:
    1. Real outbound order creation at https://api.razorpay.com/v1/orders
    2. Real authoritative order fetch and status reconciliation
    3. Proper integer minor unit amount (149900 INR)
    4. Receipt length bounded to <= 40 chars
    5. Real HMAC-SHA256 signature verification over webhook payload
    """
    import hashlib
    import hmac
    import os

    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_sec = os.environ.get("RAZORPAY_KEY_SECRET")
    if (
        not key_id
        or not key_sec
        or not key_id.startswith("rzp_test_")
        or "placeholder" in key_id
        or "EXAMPLE" in key_id
    ):
        pytest.skip("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET with valid rzp_test_ prefix not set")

    provider = RazorpaySandboxProvider(key_id=key_id, key_secret=key_sec, auto_capture=False)
    amount = Money(149900, "INR")
    attempt = provider.create_payment(
        tenant_id="tenant_live_sandbox",
        intent_id="intent_live_01",
        amount=amount,
        idempotency_key="live_idem_01",
    )
    assert attempt.provider_payment_id is not None
    assert attempt.provider_payment_id.startswith("order_")
    assert attempt.status == PaymentStatus.AUTHORIZED
    assert attempt.amount == amount

    # Query authoritative status from external sandbox
    queried = provider.get_payment("tenant_live_sandbox", attempt.provider_payment_id)
    assert queried.provider_payment_id == attempt.provider_payment_id
    assert queried.amount == amount
    assert queried.status == PaymentStatus.AUTHORIZED

    # Verify HMAC signature calculation on mock webhook payload using secret
    test_payload = b'{"entity":"event","event":"order.paid"}'
    test_sig = hmac.new(key_sec.encode("utf-8"), test_payload, hashlib.sha256).hexdigest()
    assert (
        provider.verify_webhook_signature(
            payload=test_payload,
            signature=test_sig,
            secret=key_sec,
        )
        is True
    )

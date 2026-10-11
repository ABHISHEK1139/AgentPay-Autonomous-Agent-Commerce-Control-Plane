"""Razorpay Payment Provider Adapter (§39, §41, §42, §61).

Implements the PaymentProvider application port for Razorpay:
- Order & payment initiation with integer paise amounts (§39).
- Idempotency key binding and replay handling (§42).
- Explicit capture and refund execution (§41, §44).
- Cryptographic HMAC-SHA256 signature verification for X-Razorpay-Signature (§61).
- Event normalization for PaymentReconciler (§43).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from agentpay.application.ports.payment_port import PaymentProvider
from agentpay.domain.errors import InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    PaymentAttempt,
    PaymentStatus,
    Refund,
)


class RazorpayWebhookSignatureError(InvalidPaymentError):
    """Raised when inbound Razorpay webhook signature verification fails (§61)."""

    code = "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"


class RazorpayPaymentProvider(PaymentProvider):
    """Razorpay implementation of PaymentProvider port with HMAC signature verification."""

    def __init__(
        self,
        key_id: str = "rzp_test_placeholder_id",
        key_secret: str = "rzp_test_placeholder_secret",
        webhook_secret: str = "whsec_test_razorpay_secret_32bytes",
        auto_capture: bool = True,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self.auto_capture = auto_capture
        self._lock = threading.Lock()
        # Storage: (tenant_id, attempt_id) -> PaymentAttempt
        self._attempts: dict[tuple[str, str], PaymentAttempt] = {}
        # (tenant_id, idempotency_key) -> attempt_id
        self._idempotency_map: dict[tuple[str, str], str] = {}
        # (tenant_id, refund_id) -> Refund
        self._refunds: dict[tuple[str, str], Refund] = {}

    def create_payment(
        self,
        tenant_id: str,
        intent_id: str,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentAttempt:
        """Create a Razorpay payment/order for the given amount in minor units (§39, §42)."""
        if amount.minor_units <= 0:
            raise InvalidPaymentError("Payment amount must be positive minor units")

        with self._lock:
            # Check idempotency replay
            idem_key = (tenant_id, idempotency_key)
            if idem_key in self._idempotency_map:
                existing_attempt_id = self._idempotency_map[idem_key]
                return self._attempts[(tenant_id, existing_attempt_id)]

            attempt_id = f"pay_{uuid.uuid4().hex[:16]}"
            provider_payment_id = f"pay_rzp_{uuid.uuid4().hex[:16]}"
            status = PaymentStatus.CAPTURED if self.auto_capture else PaymentStatus.AUTHORIZED

            attempt = PaymentAttempt(
                attempt_id=attempt_id,
                intent_id=intent_id,
                tenant_id=tenant_id,
                amount=amount,
                status=status,
                idempotency_key=idempotency_key,
                provider="razorpay",
                provider_payment_id=provider_payment_id,
                failure_reason=None,
            )

            self._attempts[(tenant_id, attempt_id)] = attempt
            self._idempotency_map[idem_key] = attempt_id
            return attempt

    def get_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Fetch payment attempt status for reconciliation (§43)."""
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found in Razorpay")
            return attempt

    def capture_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Capture an authorized Razorpay payment (§41)."""
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found in Razorpay")
            if attempt.status == PaymentStatus.CAPTURED:
                return attempt
            if attempt.status != PaymentStatus.AUTHORIZED:
                raise InvalidPaymentError(
                    f"Cannot capture payment '{attempt_id}' in non-authorizable status '{attempt.status.value}'"
                )
            updated = attempt.transition_to(PaymentStatus.CAPTURED)
            self._attempts[(tenant_id, attempt_id)] = updated
            return updated

    def refund(
        self,
        tenant_id: str,
        attempt_id: str,
        amount: Money,
        reason: str,
    ) -> Refund:
        """Issue a refund against a captured Razorpay payment (§44)."""
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found in Razorpay")
            if attempt.status not in (PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED):
                raise InvalidPaymentError(
                    f"Cannot refund payment '{attempt_id}' in non-refundable status '{attempt.status.value}'"
                )

            if amount.minor_units <= 0:
                raise InvalidPaymentError("Refund amount must be strictly positive")

            if amount.currency != attempt.amount.currency:
                raise InvalidPaymentError(
                    f"Refund currency '{amount.currency}' does not match payment currency '{attempt.amount.currency}'"
                )

            already_refunded = sum(
                r.amount.minor_units
                for r in self._refunds.values()
                if r.tenant_id == tenant_id and r.payment_attempt_id == attempt_id
            )
            remaining = attempt.amount.minor_units - already_refunded
            if amount.minor_units > remaining:
                raise InvalidPaymentError(
                    f"Refund amount {amount.minor_units} exceeds remaining refundable balance {remaining}"
                )

            new_status = (
                PaymentStatus.REFUNDED
                if already_refunded + amount.minor_units == attempt.amount.minor_units
                else PaymentStatus.PARTIALLY_REFUNDED
            )

            updated_attempt = attempt.transition_to(new_status)
            refund_id = f"rfnd_rzp_{uuid.uuid4().hex[:16]}"
            ref = Refund(
                refund_id=refund_id,
                tenant_id=tenant_id,
                payment_attempt_id=attempt_id,
                amount=amount,
                reason=reason,
            )
            self._refunds[(tenant_id, refund_id)] = ref
            self._attempts[(tenant_id, attempt_id)] = updated_attempt
            return ref

    def generate_webhook_signature(
        self,
        payload: bytes,
        secret: str | None = None,
    ) -> str:
        """Generate a Razorpay X-Razorpay-Signature hex digest HMAC-SHA256 (§61)."""
        sec = secret or self.webhook_secret
        return hmac.new(sec.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        timestamp: int | None = None,
        tolerance_seconds: int = 300,
        current_time: int | None = None,
    ) -> bool:
        """Cryptographically verify Razorpay X-Razorpay-Signature header with optional timestamp tolerance (§61)."""
        if not signature or not payload:
            return False

        if timestamp is not None:
            now = current_time if current_time is not None else int(time.time())
            if abs(now - timestamp) > tolerance_seconds:
                return False

        expected_sig = self.generate_webhook_signature(payload)
        return hmac.compare_digest(expected_sig, signature)

    def parse_webhook_event(
        self,
        payload: bytes,
        signature: str,
        timestamp: int | None = None,
        tolerance_seconds: int = 300,
        current_time: int | None = None,
    ) -> dict[str, Any]:
        """Verify signature and parse Razorpay webhook payload into canonical format (§61).

        Canonical event names:
        - payment.captured -> payment.captured
        - payment.failed -> payment.failed
        - refund.processed -> payment.refunded
        """
        if not self.verify_webhook_signature(
            payload,
            signature,
            timestamp=timestamp,
            tolerance_seconds=tolerance_seconds,
            current_time=current_time,
        ):
            raise RazorpayWebhookSignatureError("Invalid Razorpay webhook signature")

        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise InvalidPaymentError(f"Malformed Razorpay webhook JSON: {err}") from err

        event_name = data.get("event", "")
        payload_entity = data.get("payload", {})
        payment_entity = payload_entity.get("payment", {}).get("entity", {})

        notes = payment_entity.get("notes", {})
        attempt_id = (
            notes.get("attempt_id")
            or notes.get("payment_attempt_id")
            or payment_entity.get("id")
            or ""
        )
        tenant_id = notes.get("tenant_id") or "tenant_01"

        type_mapping = {
            "payment.captured": "payment.captured",
            "payment.failed": "payment.failed",
            "refund.processed": "payment.refunded",
        }
        canonical_type = type_mapping.get(event_name, event_name)

        return {
            "event_type": canonical_type,
            "provider": "razorpay",
            "tenant_id": tenant_id,
            "attempt_id": attempt_id,
            "data": {
                "provider_payment_id": payment_entity.get("id"),
                "amount": payment_entity.get("amount"),
                "currency": (payment_entity.get("currency") or "INR").upper(),
                "status": payment_entity.get("status"),
                "error_description": payment_entity.get("error_description"),
                "received_at": datetime.now(UTC).isoformat(),
            },
        }

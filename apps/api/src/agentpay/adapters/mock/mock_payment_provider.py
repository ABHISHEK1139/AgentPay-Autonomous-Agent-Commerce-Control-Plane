"""Mock Payment Provider simulating PSP behavior (e.g. Razorpay sandbox) (§39, §43, §112).

Supports:
- Normal automated authorization and capture.
- Simulated decline for failure scenario testing.
- Simulated timeout yielding UNKNOWN status (§43).
- Safe timeout reconciliation without duplicate financial charge.
- Idempotent request replay.
"""

from __future__ import annotations

import threading
import uuid

from agentpay.application.ports.payment_port import PaymentProvider
from agentpay.domain.errors import InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    PaymentAttempt,
    PaymentStatus,
    Refund,
)


class MockPaymentProvider(PaymentProvider):
    """Thread-safe mock payment provider supporting normal and failure modes."""

    def __init__(
        self,
        simulate_timeout: bool = False,
        simulate_decline: bool = False,
    ) -> None:
        self.simulate_timeout = simulate_timeout
        self.simulate_decline = simulate_decline
        self._lock = threading.Lock()
        # (tenant_id, attempt_id) -> PaymentAttempt
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
        with self._lock:
            # Check idempotency replay
            key = (tenant_id, idempotency_key)
            if key in self._idempotency_map:
                existing_attempt_id = self._idempotency_map[key]
                return self._attempts[(tenant_id, existing_attempt_id)]

            attempt_id = f"pay_{uuid.uuid4().hex[:16]}"
            provider_payment_id = f"rzp_mock_{uuid.uuid4().hex[:12]}"

            if self.simulate_timeout:
                status = PaymentStatus.UNKNOWN
                failure_reason = "Provider gateway timeout"
            elif self.simulate_decline:
                status = PaymentStatus.DECLINED
                failure_reason = "Card declined or insufficient balance"
            else:
                status = PaymentStatus.CAPTURED
                failure_reason = None

            attempt = PaymentAttempt(
                attempt_id=attempt_id,
                intent_id=intent_id,
                tenant_id=tenant_id,
                amount=amount,
                status=status,
                idempotency_key=idempotency_key,
                provider="mock_razorpay",
                provider_payment_id=provider_payment_id,
                failure_reason=failure_reason,
            )

            self._attempts[(tenant_id, attempt_id)] = attempt
            self._idempotency_map[key] = attempt_id
            return attempt

    def get_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found")
            return attempt

    def capture_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found")
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
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found")

            refund_id = f"rfnd_{uuid.uuid4().hex[:16]}"
            refund = Refund(
                refund_id=refund_id,
                payment_attempt_id=attempt_id,
                tenant_id=tenant_id,
                amount=amount,
                reason=reason,
            )
            self._refunds[(tenant_id, refund_id)] = refund
            # Update attempt status
            self._attempts[(tenant_id, attempt_id)] = attempt.transition_to(PaymentStatus.REFUNDED)
            return refund

    def reconcile_timeout(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Simulate reconciliation of an UNKNOWN payment status (§43, §112)."""
        with self._lock:
            attempt = self._attempts.get((tenant_id, attempt_id))
            if attempt is None:
                raise InvalidPaymentError(f"Payment attempt '{attempt_id}' not found")
            # In reconciliation, we query the upstream provider and confirm success
            reconciled = attempt.transition_to(
                PaymentStatus.CAPTURED,
                failure_reason=None,
            )
            self._attempts[(tenant_id, attempt_id)] = reconciled
            return reconciled

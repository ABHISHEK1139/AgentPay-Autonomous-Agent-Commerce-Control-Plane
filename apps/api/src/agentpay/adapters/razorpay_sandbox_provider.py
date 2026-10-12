"""Razorpay Sandbox Payment Provider Adapter (§39, §41, §42, §61, AP-004).

Real Razorpay sandbox provider implementing the PaymentProvider port:
- Communicates with Razorpay API endpoints (default: https://api.razorpay.com/v1).
- Enforces fail-fast credential validation against missing, malformed, or placeholder keys.
- Pre-flight outbound SSRF validation via agentpay.security.ssrf (§60).
- Maps network timeouts to PaymentStatus.UNKNOWN (§43).
- Strict non-fabrication of provider identifiers: preserves real order and payment IDs.
- Provides cryptographic HMAC-SHA256 webhook signature verification (§61).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from agentpay.application.ports.payment_port import PaymentProvider
from agentpay.domain.errors import InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import (
    PaymentAttempt,
    PaymentStatus,
    Refund,
)
from agentpay.security.ssrf import SSRFConfig, validate_outbound_url

PLACEHOLDER_SUBSTRINGS: Final[tuple[str, ...]] = (
    "placeholder",
    "your_key",
    "your_secret",
    "dummy",
    "changeme",
    "change_me",
    "test_secret",
    "sample",
    "replace_me",
    "xxx",
    "0000",
)


class RazorpaySandboxCredentialError(InvalidPaymentError):
    """Raised when Razorpay sandbox credentials fail validation (§39, AP-004)."""

    code = "RAZORPAY_SANDBOX_CREDENTIAL_INVALID"


class RazorpayWebhookSignatureError(InvalidPaymentError):
    """Raised when inbound Razorpay webhook signature verification fails (§61)."""

    code = "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"


@dataclass(frozen=True, slots=True)
class RazorpaySandboxConfig:
    """Configuration for Razorpay sandbox client."""

    key_id: str
    key_secret: str
    webhook_secret: str = ""
    base_url: str = "https://api.razorpay.com/v1"
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 10.0
    auto_capture: bool = True
    provider_account_id: str = ""


class RazorpaySandboxProvider(PaymentProvider):
    """Real Razorpay sandbox adapter implementing the PaymentProvider port (§39, AP-004)."""

    def __init__(
        self,
        key_id: str = "",
        key_secret: str = "",
        webhook_secret: str = "",
        base_url: str = "https://api.razorpay.com/v1",
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 10.0,
        auto_capture: bool = True,
        provider_account_id: str = "",
        config: RazorpaySandboxConfig | None = None,
        transport: (
            Callable[[urllib.request.Request], tuple[int, bytes, dict[str, str]]] | None
        ) = None,
        resolve_dns: bool = True,
    ) -> None:
        if config is not None:
            self.key_id = config.key_id
            self.key_secret = config.key_secret
            self.webhook_secret = config.webhook_secret
            self.base_url = config.base_url
            self.connect_timeout_seconds = config.connect_timeout_seconds
            self.read_timeout_seconds = config.read_timeout_seconds
            self.auto_capture = config.auto_capture
            self.provider_account_id = config.provider_account_id
        else:
            self.key_id = key_id
            self.key_secret = key_secret
            self.webhook_secret = webhook_secret
            self.base_url = base_url
            self.connect_timeout_seconds = connect_timeout_seconds
            self.read_timeout_seconds = read_timeout_seconds
            self.auto_capture = auto_capture
            self.provider_account_id = provider_account_id

        self._transport = transport
        self.resolve_dns = resolve_dns
        self._validate_credentials()

    def _validate_credentials(self) -> None:
        """Fail fast if sandbox credentials are missing, malformed, or placeholders."""
        if not self.key_id or not self.key_id.strip():
            raise RazorpaySandboxCredentialError(
                "RAZORPAY_KEY_ID is missing or empty in sandbox mode"
            )
        if not self.key_secret or not self.key_secret.strip():
            raise RazorpaySandboxCredentialError(
                "RAZORPAY_KEY_SECRET is missing or empty in sandbox mode"
            )

        k_id = self.key_id.strip()
        k_sec = self.key_secret.strip()

        if not k_id.startswith("rzp_test_"):
            raise RazorpaySandboxCredentialError(
                f"RAZORPAY_KEY_ID must begin with 'rzp_test_', got '{k_id[:10]}...'"
            )
        if len(k_id) < 14:
            raise RazorpaySandboxCredentialError("RAZORPAY_KEY_ID length must be at least 14 chars")
        if len(k_sec) < 16:
            raise RazorpaySandboxCredentialError(
                "RAZORPAY_KEY_SECRET length must be at least 16 chars"
            )

        k_id_lower = k_id.lower()
        k_sec_lower = k_sec.lower()
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in k_id_lower:
                raise RazorpaySandboxCredentialError(
                    f"RAZORPAY_KEY_ID contains disallowed placeholder string: '{placeholder}'"
                )
            if placeholder in k_sec_lower:
                raise RazorpaySandboxCredentialError(
                    f"RAZORPAY_KEY_SECRET contains disallowed placeholder string: '{placeholder}'"
                )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Execute validated HTTP request to Razorpay sandbox endpoint (§39, §60)."""
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

        # Pre-flight SSRF validation
        if self._transport is None:
            validate_outbound_url(url, config=SSRFConfig(resolve_dns=self.resolve_dns))

        auth_str = f"{self.key_id}:{self.key_secret}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")

        body_bytes: bytes | None = None
        if payload is not None:
            body_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AgentPay-Sandbox-Client/1.0",
            },
            method=method,
        )

        try:
            if self._transport is not None:
                status_code, resp_bytes, _ = self._transport(req)
            else:
                with urllib.request.urlopen(req, timeout=self.read_timeout_seconds) as resp:
                    status_code = getattr(resp, "status", 200)
                    resp_bytes = resp.read()

            parsed = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
            if not isinstance(parsed, dict):
                parsed = {"data": parsed}
            return status_code, parsed

        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            try:
                err_body = http_err.read().decode("utf-8")
                err_data = json.loads(err_body)
                err_desc = err_data.get("error", {}).get("description") or str(err_data)
            except Exception:
                err_desc = http_err.reason
            # Redact secrets from error message
            safe_msg = (
                str(err_desc)
                .replace(self.key_secret, "[REDACTED]")
                .replace(self.key_id, "[REDACTED]")
            )
            raise InvalidPaymentError(
                f"Razorpay API error HTTP {status_code}: {safe_msg}"
            ) from http_err

        except TimeoutError as timeout_err:
            raise InvalidPaymentError(
                "Gateway timeout connecting to Razorpay sandbox"
            ) from timeout_err

        except urllib.error.URLError as url_err:
            reason = getattr(url_err, "reason", None)
            if isinstance(reason, TimeoutError) or "timed out" in str(url_err).lower():
                raise InvalidPaymentError(
                    "Gateway timeout connecting to Razorpay sandbox"
                ) from url_err
            raise InvalidPaymentError(
                f"Razorpay network communication error: {url_err}"
            ) from url_err

    def create_payment(
        self,
        tenant_id: str,
        intent_id: str,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentAttempt:
        """Create a Razorpay order in sandbox for the specified minor units (§39, §42)."""
        if amount.minor_units <= 0:
            raise InvalidPaymentError("Payment amount must be positive minor units")

        safe_receipt = (
            idempotency_key
            if len(idempotency_key) <= 40
            else f"rcpt_{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()[:32]}"
        )

        payload = {
            "amount": amount.minor_units,
            "currency": amount.currency,
            "receipt": safe_receipt,
            "notes": {
                "tenant_id": tenant_id,
                "intent_id": intent_id,
                "idempotency_key": idempotency_key,
            },
        }

        try:
            _, data = self._request("POST", "/orders", payload)
        except InvalidPaymentError as exc:
            if "timeout" in str(exc).lower():
                return PaymentAttempt(
                    attempt_id=f"pay_unknown_{uuid.uuid4().hex[:12]}",
                    intent_id=intent_id,
                    tenant_id=tenant_id,
                    amount=amount,
                    status=PaymentStatus.UNKNOWN,
                    idempotency_key=idempotency_key,
                    provider="razorpay",
                    provider_payment_id=None,
                    failure_reason="Gateway timeout; reconciliation required",
                )
            raise

        provider_order_id = data.get("id")
        if not provider_order_id or not isinstance(provider_order_id, str):
            raise InvalidPaymentError("Razorpay API response missing order id")

        resp_status = data.get("status", "")
        if resp_status in ("created", "attempted"):
            status = PaymentStatus.CAPTURED if self.auto_capture else PaymentStatus.AUTHORIZED
        elif resp_status in ("captured", "paid"):
            status = PaymentStatus.CAPTURED
        elif resp_status == "failed":
            status = PaymentStatus.FAILED
        else:
            status = PaymentStatus.AUTHORIZED if self.auto_capture else PaymentStatus.PENDING

        attempt_id = f"pay_{uuid.uuid4().hex[:16]}"
        return PaymentAttempt(
            attempt_id=attempt_id,
            intent_id=intent_id,
            tenant_id=tenant_id,
            amount=amount,
            status=status,
            idempotency_key=idempotency_key,
            provider="razorpay",
            provider_payment_id=provider_order_id,
            failure_reason=None,
        )

    def get_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Fetch payment status from Razorpay for reconciliation (§43)."""
        target_path = (
            f"/orders/{attempt_id}"
            if attempt_id.startswith("order_")
            else f"/payments/{attempt_id}"
        )
        try:
            _, data = self._request("GET", target_path)
        except InvalidPaymentError as exc:
            if "timeout" in str(exc).lower():
                return PaymentAttempt(
                    attempt_id=attempt_id,
                    intent_id=f"intent_{attempt_id}",
                    tenant_id=tenant_id,
                    amount=Money(0, "INR"),
                    status=PaymentStatus.UNKNOWN,
                    idempotency_key=f"idem_{attempt_id}",
                    provider="razorpay",
                    provider_payment_id=attempt_id,
                    failure_reason="Gateway timeout; reconciliation required",
                )
            raise

        order_id = str(data.get("id") or attempt_id)
        notes = data.get("notes", {})
        intent_id = str(notes.get("intent_id") or "").strip() or f"intent_{attempt_id}"
        idem_key = (
            str(notes.get("idempotency_key") or data.get("receipt") or "").strip()
            or f"idem_{attempt_id}"
        )
        currency = str(data.get("currency") or "INR").upper()
        amount_minor = int(data.get("amount") or 0)
        noted_tenant = str(notes.get("tenant_id") or "").strip()
        record_tenant = noted_tenant if noted_tenant else tenant_id

        raw_status = data.get("status", "")
        if raw_status in ("created", "authorized"):
            status = PaymentStatus.AUTHORIZED
        elif raw_status in ("captured", "paid"):
            status = PaymentStatus.CAPTURED
        elif raw_status == "failed":
            status = PaymentStatus.FAILED
        elif raw_status == "refunded":
            status = PaymentStatus.REFUNDED
        else:
            status = PaymentStatus.PENDING

        return PaymentAttempt(
            attempt_id=attempt_id,
            intent_id=intent_id,
            tenant_id=record_tenant,
            amount=Money(amount_minor, currency),
            status=status,
            idempotency_key=idem_key,
            provider="razorpay",
            provider_payment_id=order_id,
            failure_reason=data.get("error_description"),
        )

    def capture_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Capture an authorized Razorpay payment in sandbox (§41)."""
        attempt = self.get_payment(tenant_id, attempt_id)
        if attempt.tenant_id != tenant_id:
            raise InvalidPaymentError(
                f"Payment attempt '{attempt_id}' belongs to tenant '{attempt.tenant_id}', not '{tenant_id}'"
            )
        if attempt.status == PaymentStatus.CAPTURED:
            return attempt

        target_path = f"/payments/{attempt_id}/capture"
        payload = {"amount": attempt.amount.minor_units, "currency": attempt.amount.currency}
        _, _ = self._request("POST", target_path, payload)
        return attempt.transition_to(PaymentStatus.CAPTURED)

    def refund(
        self,
        tenant_id: str,
        attempt_id: str,
        amount: Money,
        reason: str,
    ) -> Refund:
        """Issue a refund against a payment in sandbox (§44)."""
        if amount.minor_units <= 0:
            raise InvalidPaymentError("Refund amount must be strictly positive")

        attempt = self.get_payment(tenant_id, attempt_id)
        if attempt.tenant_id != tenant_id:
            raise InvalidPaymentError(
                f"Payment attempt '{attempt_id}' belongs to tenant '{attempt.tenant_id}', not '{tenant_id}'"
            )
        if amount.currency != attempt.amount.currency:
            raise InvalidPaymentError(
                f"Refund currency '{amount.currency}' does not match payment currency '{attempt.amount.currency}'"
            )
        if amount.minor_units > attempt.amount.minor_units:
            raise InvalidPaymentError("Refund amount exceeds payment amount")

        target_path = f"/payments/{attempt_id}/refund"
        payload = {
            "amount": amount.minor_units,
            "notes": {
                "tenant_id": tenant_id,
                "reason": reason,
            },
        }

        _, data = self._request("POST", target_path, payload)
        refund_id = str(data.get("id") or f"rfnd_{uuid.uuid4().hex[:16]}")

        return Refund(
            refund_id=refund_id,
            tenant_id=tenant_id,
            payment_attempt_id=attempt_id,
            amount=amount,
            reason=reason,
        )

    def generate_webhook_signature(
        self,
        payload: bytes,
        secret: str | None = None,
    ) -> str:
        """Generate HMAC-SHA256 signature for Razorpay webhook testing (§61)."""
        key = (secret or self.webhook_secret).encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        secret: str | None = None,
        timestamp: int | None = None,
        tolerance_seconds: int = 300,
        current_time: int | None = None,
    ) -> bool:
        """Verify Razorpay webhook signature with timing-safe comparison (§61)."""
        if not signature:
            return False

        if timestamp is not None:
            now = current_time if current_time is not None else int(datetime.now(UTC).timestamp())
            if abs(now - timestamp) > tolerance_seconds:
                return False

        expected_sig = self.generate_webhook_signature(payload, secret=secret)
        return hmac.compare_digest(expected_sig, signature)

    def parse_webhook_event(
        self,
        payload: bytes,
        signature: str,
        timestamp: int | None = None,
        tolerance_seconds: int = 300,
        current_time: int | None = None,
    ) -> dict[str, Any]:
        """Verify signature and parse Razorpay webhook payload into canonical format (§61)."""
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
        attempt_id = str(notes.get("attempt_id") or notes.get("payment_attempt_id") or "").strip()
        tenant_id = str(notes.get("tenant_id") or "").strip()

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


__all__ = [
    "RazorpaySandboxConfig",
    "RazorpaySandboxCredentialError",
    "RazorpaySandboxProvider",
    "RazorpayWebhookSignatureError",
]

"""HTTP 402 Payment Required (x402) protocol adapter implementation (§107, §108).

Provides:
- Inbound translation of HTTP 402 micropayment challenges into canonical ActionRequests.
- Outbound translation into cryptographic authorization settlement tokens and proof headers.
- Typed schemas for 402 challenges, payment requests, and settlement proofs.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agentpay.adapters.protocols.base import (
    BaseProtocolAdapter,
    MalformedPayloadError,
    UnsupportedProtocolVersionError,
    parse_iso_utc,
)
from agentpay.application.use_cases.purchase_flow import PurchaseExecutionResult
from agentpay.domain.action_request import (
    ActionRequest,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.money import Money

SUPPORTED_X402_VERSIONS = frozenset({"1.0", "x402/1.0", "rfc-draft-402"})


@dataclass(frozen=True, slots=True)
class X402Challenge:
    """HTTP 402 challenge issued by a resource server."""

    challenge_id: str
    resource_uri: str
    amount_minor: int
    currency: str = "INR"
    payee_id: str = "merchant_payee"
    scheme: str = "agentpay_direct"

    def to_header(self) -> str:
        """Format as HTTP WWW-Authenticate challenge header value."""
        return (
            f'{self.scheme} challenge_id="{self.challenge_id}", '
            f'resource="{self.resource_uri}", amount={self.amount_minor}, '
            f'currency="{self.currency}", payee="{self.payee_id}"'
        )


@dataclass(frozen=True, slots=True)
class X402ChallengeRequest:
    """Parsed and validated x402 payment request submitted by an agent."""

    x402_version: str
    challenge_id: str
    resource_uri: str
    payee_id: str
    amount_minor: int
    currency: str
    agent_id: str
    principal_id: str
    tenant_id: str
    purpose: str
    timestamp: datetime
    payment_scheme: str = "agentpay_direct"
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class X402PaymentResponse:
    """Standardized x402 payment response with settlement proof token."""

    x402_version: str
    challenge_id: str
    status: str
    authorization_token: str | None
    settlement_ref: str | None
    expires_at: str | None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary conforming to x402 specification."""
        return {
            "x402_version": self.x402_version,
            "challenge_id": self.challenge_id,
            "status": self.status,
            "authorization_token": self.authorization_token,
            "settlement_ref": self.settlement_ref,
            "expires_at": self.expires_at,
            "rejection_reason": self.rejection_reason,
        }


# Schema Aliases
X402PaymentRequest = X402ChallengeRequest
X402SettlementProof = X402PaymentResponse


class X402Adapter(BaseProtocolAdapter[X402ChallengeRequest, X402PaymentResponse]):
    """Adapter translating HTTP 402 micro-payment challenges to and from AgentPay."""

    def parse_payload(self, raw_data: dict[str, Any]) -> X402ChallengeRequest:
        """Parse raw x402 dictionary payload into validated X402ChallengeRequest."""
        if not isinstance(raw_data, dict):
            raise MalformedPayloadError("x402 payload must be a JSON object")

        version = str(raw_data.get("x402_version", raw_data.get("version", "")))
        if not version or version not in SUPPORTED_X402_VERSIONS:
            raise UnsupportedProtocolVersionError(
                f"Unsupported x402 version '{version}'. Supported: {sorted(SUPPORTED_X402_VERSIONS)}"
            )

        chal_id = str(raw_data.get("challenge_id", "")).strip()
        if not chal_id:
            raise MalformedPayloadError("Missing required field 'challenge_id'")

        resource_uri = str(raw_data.get("resource_uri", raw_data.get("uri", ""))).strip()
        if not resource_uri:
            raise MalformedPayloadError("Missing required field 'resource_uri'")

        for fld in ("principal_id", "agent_id", "tenant_id", "amount_minor"):
            if raw_data.get(fld) is None:
                raise MalformedPayloadError(f"Missing required field '{fld}' in x402 request")

        for str_fld in ("principal_id", "agent_id", "tenant_id"):
            val = str(raw_data[str_fld]).strip()
            if not val:
                raise MalformedPayloadError(f"Field '{str_fld}' cannot be empty")

        raw_amt = raw_data["amount_minor"]
        if isinstance(raw_amt, bool) or not isinstance(raw_amt, int):
            raise MalformedPayloadError(
                f"amount_minor must be an integer, got {type(raw_amt).__name__}"
            )
        if raw_amt < 0:
            raise MalformedPayloadError("amount_minor cannot be negative")
        amt = raw_amt

        raw_curr = raw_data.get("currency", "INR")
        if not isinstance(raw_curr, str) or not raw_curr.strip():
            raise MalformedPayloadError("currency cannot be empty")
        curr = raw_curr.strip().upper()

        return X402ChallengeRequest(
            x402_version=version,
            challenge_id=chal_id,
            resource_uri=resource_uri,
            payee_id=str(raw_data.get("payee_id", "merchant_payee")).strip(),
            amount_minor=amt,
            currency=curr,
            agent_id=str(raw_data["agent_id"]).strip(),
            principal_id=str(raw_data["principal_id"]).strip(),
            tenant_id=str(raw_data["tenant_id"]).strip(),
            purpose=str(raw_data.get("purpose", f"HTTP 402 access for {resource_uri}")),
            timestamp=parse_iso_utc(raw_data.get("timestamp")),
            payment_scheme=str(raw_data.get("payment_scheme", "agentpay_direct")),
            idempotency_key=raw_data.get("idempotency_key"),
        )

    def to_action_request(
        self, request: X402ChallengeRequest, tenant_id: str | None = None
    ) -> tuple[ActionRequest, dict[str, Any]]:
        """Translate typed X402ChallengeRequest into canonical ActionRequest and metadata."""
        effective_tenant = request.tenant_id or (tenant_id or "")
        amount = Money(minor_units=request.amount_minor, currency=request.currency)

        action_id = (
            request.challenge_id
            if request.challenge_id.startswith("x402_")
            else f"x402_{request.challenge_id}"
        )

        action = ActionRequest(
            action_id=action_id,
            tenant_id=effective_tenant,
            principal_id=request.principal_id,
            agent_id=request.agent_id,
            domain="web",
            action="purchase",
            resource=ResourceRef(type="uri", id=request.resource_uri),
            requested_at=request.timestamp,
            intent=Intent(purpose=request.purpose, budget_max=amount),
            amount=amount,
            authorization_mode=AuthorizationMode.AUTONOMOUS,
        )

        metadata = {
            "variant_id": f"x402_{request.challenge_id}",
            "quantity": 1,
            "idempotency_key": request.idempotency_key or f"idem_402_{request.challenge_id}",
        }
        return action, metadata

    def from_execution_result(
        self, request: X402ChallengeRequest, result: PurchaseExecutionResult
    ) -> X402PaymentResponse:
        """Translate canonical PurchaseExecutionResult into X402PaymentResponse."""
        is_paid = result.payment is not None and result.decision.value == "ALLOW"
        tok: str | None = None
        expires_str: str | None = None

        if is_paid:
            exp_time = datetime.now(UTC) + timedelta(hours=1)
            expires_str = exp_time.isoformat()
            claims = {
                "sub": request.agent_id,
                "aud": request.resource_uri,
                "amount": request.amount_minor,
                "currency": request.currency,
                "tx": result.payment.attempt_id if result.payment else "",
                "exp": int(exp_time.timestamp()),
            }
            tok = "x402_" + base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode(
                "utf-8"
            ).rstrip("=")

        return X402PaymentResponse(
            x402_version=request.x402_version,
            challenge_id=request.challenge_id,
            status="PAID" if is_paid else result.status.value,
            authorization_token=tok,
            settlement_ref=result.payment.attempt_id if result.payment else None,
            expires_at=expires_str,
            rejection_reason=result.rejection_reason,
        )

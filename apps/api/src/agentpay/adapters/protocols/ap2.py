"""Agent Payment Protocol (AP2) adapter implementation (§107, §108).

Provides:
- Inbound translation of AP2 payment mandates and payment requests into canonical ActionRequests.
- Outbound translation of settlement results into standardized AP2 responses.
- Typed schemas for AP2 payers, payees, mandates, and payment responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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

SUPPORTED_AP2_VERSIONS = frozenset({"1.0", "ap2/1.0", "2026-01"})


@dataclass(frozen=True, slots=True)
class Ap2Payer:
    """Payer identity and authorization metadata in AP2."""

    principal_id: str
    agent_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class Ap2Payee:
    """Payee settlement recipient in AP2."""

    merchant_id: str
    order_ref: str
    resource_type: str = "order"


@dataclass(frozen=True, slots=True)
class Ap2PaymentRequest:
    """Parsed and validated AP2 payment mandate request."""

    ap2_version: str
    payment_request_id: str
    mandate_id: str | None
    payer: Ap2Payer
    payee: Ap2Payee
    amount_minor: int
    currency: str
    purpose: str
    timestamp: datetime
    max_authorized_minor: int | None = None
    autonomous: bool = True
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class Ap2PaymentResponse:
    """Standardized AP2 payment response payload."""

    ap2_version: str
    payment_request_id: str
    mandate_id: str | None
    status: str
    decision: str
    transaction_id: str | None
    approval_id: str | None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary conforming to AP2 specification."""
        return {
            "ap2_version": self.ap2_version,
            "payment_request_id": self.payment_request_id,
            "mandate_id": self.mandate_id,
            "status": self.status,
            "decision": self.decision,
            "transaction_id": self.transaction_id,
            "approval_id": self.approval_id,
            "rejection_reason": self.rejection_reason,
        }


# Schema Aliases
AP2Payer = Ap2Payer
AP2Payee = Ap2Payee
AP2PaymentMandateRequest = Ap2PaymentRequest
AP2Response = Ap2PaymentResponse


class Ap2Adapter(BaseProtocolAdapter[Ap2PaymentRequest, Ap2PaymentResponse]):
    """Adapter translating AP2 messages to and from AgentPay canonical models."""

    def parse_payload(self, raw_data: Any) -> Ap2PaymentRequest:
        """Parse raw AP2 dictionary payload into validated Ap2PaymentRequest."""
        if not isinstance(raw_data, dict) or not raw_data:
            raise MalformedPayloadError("AP2 payload must be a non-empty JSON object")

        version = str(raw_data.get("ap2_version", raw_data.get("version", "")))
        if not version or version not in SUPPORTED_AP2_VERSIONS:
            raise UnsupportedProtocolVersionError(
                f"Unsupported AP2 version '{version}'. Supported: {sorted(SUPPORTED_AP2_VERSIONS)}"
            )

        req_id = str(raw_data.get("payment_request_id", raw_data.get("request_id", "")))
        if not req_id:
            raise MalformedPayloadError("Missing required field 'payment_request_id'")

        payer_data = raw_data.get("payer")
        if not isinstance(payer_data, dict):
            raise MalformedPayloadError("Missing or invalid 'payer' block in AP2 payload")

        for fld in ("principal_id", "agent_id", "tenant_id"):
            if not payer_data.get(fld):
                raise MalformedPayloadError(f"Missing required payer field '{fld}'")

        payer = Ap2Payer(
            principal_id=str(payer_data["principal_id"]),
            agent_id=str(payer_data["agent_id"]),
            tenant_id=str(payer_data["tenant_id"]),
        )

        payee_data = raw_data.get("payee")
        if not isinstance(payee_data, dict):
            raise MalformedPayloadError("Missing or invalid 'payee' block in AP2 payload")

        merchant_id = payee_data.get("merchant_id", "merchant_default")
        order_ref = payee_data.get("order_ref", req_id)
        resource_type = payee_data.get("resource_type", "order")

        payee = Ap2Payee(
            merchant_id=str(merchant_id),
            order_ref=str(order_ref),
            resource_type=str(resource_type),
        )

        amount_data = raw_data.get("amount")
        if isinstance(amount_data, dict):
            raw_val = amount_data.get("minor_units")
            if isinstance(raw_val, bool) or not isinstance(raw_val, int):
                raise MalformedPayloadError(
                    f"amount.minor_units must be an integer, got {type(raw_val).__name__}"
                )
            amt_val = raw_val
            curr_val = str(amount_data.get("currency", "INR")).upper()
        else:
            if "amount_minor" not in raw_data:
                raise MalformedPayloadError("Missing required field 'amount' or 'amount_minor'")
            raw_val = raw_data.get("amount_minor")
            if isinstance(raw_val, bool) or not isinstance(raw_val, int):
                raise MalformedPayloadError(
                    f"amount_minor must be an integer, got {type(raw_val).__name__}"
                )
            amt_val = raw_val
            curr_val = str(raw_data.get("currency", "INR")).upper()

        if amt_val < 0:
            raise MalformedPayloadError("Amount minor units cannot be negative")

        constraints = raw_data.get("constraints", {})
        valid_until_raw = raw_data.get("valid_until") or (
            constraints.get("valid_until") if isinstance(constraints, dict) else None
        )
        if valid_until_raw:
            try:
                valid_until_dt = parse_iso_utc(str(valid_until_raw))
            except Exception as err:
                raise MalformedPayloadError(
                    f"Invalid valid_until timestamp: {valid_until_raw}"
                ) from err
            if valid_until_dt is None:
                raise MalformedPayloadError(f"Invalid valid_until timestamp: {valid_until_raw}")
            if valid_until_dt < datetime.now(UTC):
                raise MalformedPayloadError(
                    f"AP2 mandate has expired (valid_until: {valid_until_dt.isoformat()})"
                )

        max_auth = (
            constraints.get("max_authorized_minor")
            if isinstance(constraints, dict) and "max_authorized_minor" in constraints
            else raw_data.get("max_authorized_minor")
        )
        if max_auth is not None:
            if isinstance(max_auth, bool) or not isinstance(max_auth, int):
                raise MalformedPayloadError(
                    f"max_authorized_minor must be an integer, got {type(max_auth).__name__}"
                )
            if max_auth < 0:
                raise MalformedPayloadError("max_authorized_minor cannot be negative")

        mode_str = str(
            constraints.get("authorization_mode")
            if isinstance(constraints, dict) and constraints.get("authorization_mode")
            else raw_data.get("authorization_mode", "AUTONOMOUS")
        ).upper()

        return Ap2PaymentRequest(
            ap2_version=version,
            payment_request_id=req_id,
            mandate_id=raw_data.get("mandate_id"),
            payer=payer,
            payee=payee,
            amount_minor=amt_val,
            currency=curr_val,
            purpose=str(raw_data.get("purpose", "AP2 Payment Mandate Settlement")),
            timestamp=parse_iso_utc(raw_data.get("timestamp")),
            max_authorized_minor=max_auth,
            autonomous=(mode_str == "AUTONOMOUS"),
            idempotency_key=raw_data.get("idempotency_key"),
        )

    def to_action_request(
        self, request: Any, tenant_id: str | None = None
    ) -> tuple[ActionRequest, dict[str, Any]]:
        """Translate typed Ap2PaymentRequest into canonical ActionRequest and metadata."""
        if not isinstance(request, Ap2PaymentRequest):
            if isinstance(request, dict):
                request = self.parse_payload(request)
            else:
                raise MalformedPayloadError(
                    f"Expected Ap2PaymentRequest or dictionary payload, got {type(request).__name__}"
                )

        effective_tenant = (
            request.payer.tenant_id
            if request.payer and getattr(request.payer, "tenant_id", None)
            else (tenant_id or "")
        )
        if (
            not effective_tenant
            or "\x00" in effective_tenant
            or "\n" in effective_tenant
            or "\r" in effective_tenant
        ):
            raise MalformedPayloadError(
                f"Invalid tenant_id '{effective_tenant}': cannot be empty or contain control characters"
            )

        amount = Money(minor_units=request.amount_minor, currency=request.currency)

        budget_money = (
            Money(minor_units=request.max_authorized_minor, currency=request.currency)
            if request.max_authorized_minor is not None
            else None
        )

        mode = AuthorizationMode.AUTONOMOUS if request.autonomous else AuthorizationMode.SUPERVISED

        action_id = (
            request.payment_request_id
            if request.payment_request_id.startswith("ap2_")
            else f"ap2_{request.payment_request_id}"
        )

        action = ActionRequest(
            action_id=action_id,
            tenant_id=effective_tenant,
            principal_id=request.payer.principal_id,
            agent_id=request.payer.agent_id,
            domain="payments",
            action="purchase",
            resource=ResourceRef(type=request.payee.resource_type, id=request.payee.order_ref),
            requested_at=request.timestamp,
            intent=Intent(purpose=request.purpose, budget_max=budget_money),
            amount=amount,
            authorization_mode=mode,
        )

        metadata = {
            "variant_id": request.payee.order_ref,
            "quantity": 1,
            "idempotency_key": request.idempotency_key,
        }
        return action, metadata

    def from_execution_result(
        self, request: Ap2PaymentRequest, result: PurchaseExecutionResult
    ) -> Ap2PaymentResponse:
        """Translate canonical PurchaseExecutionResult into Ap2PaymentResponse."""
        return Ap2PaymentResponse(
            ap2_version=request.ap2_version,
            payment_request_id=request.payment_request_id,
            mandate_id=request.mandate_id,
            status=result.status.value,
            decision=result.decision.value,
            transaction_id=result.payment.attempt_id if result.payment else None,
            approval_id=result.approval.approval_id if result.approval else None,
            rejection_reason=result.rejection_reason,
        )


AP2Adapter = Ap2Adapter

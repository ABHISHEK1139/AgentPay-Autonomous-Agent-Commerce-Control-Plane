"""Universal Agent Protocol (UAP) adapter implementation (§107, §108).

Provides:
- Inbound translation of UAP JSON-RPC and message capability calls into canonical ActionRequests.
- Outbound translation of execution results into standard UAP JSON-RPC 2.0 responses.
- Typed schemas for UAP headers, messages, responses, and errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentpay.adapters.protocols.base import (
    BaseProtocolAdapter,
    MalformedPayloadError,
    UnsupportedActionError,
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

SUPPORTED_UAP_VERSIONS = frozenset({"1.0", "uap/1.0", "2.0"})


@dataclass(frozen=True, slots=True)
class UapHeader:
    """Routing and identity header for UAP messages."""

    sender: str
    recipient: str
    tenant: str
    principal: str


@dataclass(frozen=True, slots=True)
class UapError:
    """JSON-RPC and UAP compliant error object."""

    code: int | str
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize error to dictionary."""
        res: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            res["data"] = self.data
        return res


@dataclass(frozen=True, slots=True)
class UapMessage:
    """Parsed and validated UAP message."""

    uap_version: str
    message_id: str
    header: UapHeader
    capability: str
    parameters: dict[str, Any]
    timestamp: datetime
    constraints: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class UapResponse:
    """Standardized UAP response payload with JSON-RPC compatibility."""

    uap_version: str
    message_id: str
    reply_to: str
    status: str
    result: dict[str, Any] | None
    error: UapError | None = None
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary conforming to UAP specification."""
        res: dict[str, Any] = {
            "uap_version": self.uap_version,
            "message_id": self.message_id,
            "reply_to": self.reply_to,
            "status": self.status,
            "result": self.result,
            "error": self.error.to_dict() if self.error else None,
        }
        if self.trace_id:
            res["trace_id"] = self.trace_id
        return res


# Schema Aliases
UAPHeader = UapHeader
UAPRequest = UapMessage
UAPResponse = UapResponse
UAPError = UapError


class UapAdapter(BaseProtocolAdapter[UapMessage, UapResponse]):
    """Adapter translating UAP RPC messages to and from AgentPay canonical models."""

    def parse_payload(self, raw_data: dict[str, Any]) -> UapMessage:
        """Parse raw UAP dictionary payload into validated UapMessage."""
        if not isinstance(raw_data, dict):
            raise MalformedPayloadError("UAP payload must be a JSON object")

        # Support JSON-RPC 2.0 or UAP version format
        version = str(
            raw_data.get("uap_version", raw_data.get("jsonrpc", raw_data.get("version", "")))
        )
        if not version or version not in SUPPORTED_UAP_VERSIONS:
            raise UnsupportedProtocolVersionError(
                f"Unsupported UAP version '{version}'. Supported: {sorted(SUPPORTED_UAP_VERSIONS)}"
            )

        msg_id = str(raw_data.get("message_id", raw_data.get("id", "")))
        if not msg_id:
            raise MalformedPayloadError("Missing required field 'message_id' or 'id'")

        # Handle header (UAP style or fallback for JSON-RPC style)
        hdr_data = raw_data.get("header")
        if isinstance(hdr_data, dict):
            sender = str(hdr_data.get("sender", ""))
            tenant = str(hdr_data.get("tenant", ""))
            principal = str(hdr_data.get("principal", ""))
            recipient = str(hdr_data.get("recipient", "urn:agentpay:service:control_plane"))
        else:
            # Fallbacks if flat payload
            sender = str(raw_data.get("sender", raw_data.get("agent_id", "")))
            tenant = str(raw_data.get("tenant", raw_data.get("tenant_id", "")))
            principal = str(raw_data.get("principal", raw_data.get("principal_id", "")))
            recipient = "urn:agentpay:service:control_plane"

        if not sender or not sender.strip():
            raise MalformedPayloadError("Missing sender in UAP request")
        if not tenant or not tenant.strip():
            raise MalformedPayloadError("Missing tenant in UAP request")
        if not principal or not principal.strip():
            raise MalformedPayloadError("Missing principal in UAP request")

        header = UapHeader(
            sender=sender.strip(),
            recipient=recipient.strip(),
            tenant=tenant.strip(),
            principal=principal.strip(),
        )

        # Capability / method
        payload_data = raw_data.get("payload")
        if isinstance(payload_data, dict):
            capability = str(payload_data.get("capability", payload_data.get("method", "")))
            parameters = payload_data.get("parameters", payload_data.get("params", {}))
            constraints = payload_data.get("constraints", {})
        else:
            capability = str(raw_data.get("capability", raw_data.get("method", "")))
            parameters = raw_data.get("parameters", raw_data.get("params", {}))
            constraints = raw_data.get("constraints", {})

        if not capability:
            raise MalformedPayloadError("Missing required field 'capability' or 'method'")
        if not isinstance(parameters, dict):
            raise MalformedPayloadError("Parameters must be a JSON object")

        if "quantity" in parameters:
            raw_qty = parameters["quantity"]
            if isinstance(raw_qty, bool) or not isinstance(raw_qty, int):
                raise MalformedPayloadError(
                    f"quantity must be an integer, got {type(raw_qty).__name__}"
                )
            if raw_qty <= 0:
                raise MalformedPayloadError("quantity must be greater than zero")

        for price_key in ("price_minor", "amount_minor", "amount"):
            if price_key in parameters:
                raw_price = parameters[price_key]
                if isinstance(raw_price, bool) or not isinstance(raw_price, int):
                    raise MalformedPayloadError(
                        f"{price_key} must be an integer, got {type(raw_price).__name__}"
                    )
                if raw_price < 0:
                    raise MalformedPayloadError(f"{price_key} cannot be negative")

        for budget_key in ("budget_cap_minor", "budget_max_minor"):
            if budget_key in parameters:
                raw_budget = parameters[budget_key]
                if isinstance(raw_budget, bool) or not isinstance(raw_budget, int):
                    raise MalformedPayloadError(
                        f"{budget_key} must be an integer, got {type(raw_budget).__name__}"
                    )
                if raw_budget < 0:
                    raise MalformedPayloadError(f"{budget_key} cannot be negative")

        return UapMessage(
            uap_version=version,
            message_id=msg_id,
            header=header,
            capability=capability,
            parameters=parameters,
            timestamp=parse_iso_utc(raw_data.get("timestamp")),
            constraints=constraints if isinstance(constraints, dict) else {},
            trace_id=raw_data.get("trace_id"),
        )

    def to_action_request(
        self, request: UapMessage, tenant_id: str | None = None
    ) -> tuple[ActionRequest, dict[str, Any]]:
        """Translate typed UapMessage into canonical ActionRequest and metadata."""
        effective_tenant = request.header.tenant or (tenant_id or "")

        parts = request.capability.split(".")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise UnsupportedActionError(
                f"Capability must be format 'domain.action', got '{request.capability}'"
            )
        domain, action_name = parts[0], parts[1]

        sender = request.header.sender
        agent_id = sender.split(":")[-1] if ":" in sender else sender

        p = request.parameters
        resource_id = str(p.get("product_id", p.get("resource_id", p.get("id", "")))).strip()
        if not resource_id:
            raise MalformedPayloadError("Missing 'product_id' or 'resource_id' in UAP parameters")

        variant_id = str(p.get("variant_id", resource_id)).strip()

        raw_quantity = p.get("quantity", 1)
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
            raise MalformedPayloadError(
                f"quantity must be an integer, got {type(raw_quantity).__name__}"
            )
        if raw_quantity <= 0:
            raise MalformedPayloadError("quantity must be greater than zero")
        quantity = raw_quantity

        raw_price = p.get("price_minor", p.get("amount_minor", p.get("amount", 0)))
        if isinstance(raw_price, bool) or not isinstance(raw_price, int):
            raise MalformedPayloadError(
                f"price_minor must be an integer, got {type(raw_price).__name__}"
            )
        if raw_price < 0:
            raise MalformedPayloadError("price_minor cannot be negative")
        price_minor = raw_price

        raw_curr = p.get("currency", "INR")
        if not isinstance(raw_curr, str) or not raw_curr.strip():
            raise MalformedPayloadError("currency cannot be empty")
        currency = raw_curr.strip().upper()
        amount = Money(minor_units=price_minor, currency=currency)

        budget_val = p.get("budget_cap_minor", p.get("budget_max_minor"))
        if budget_val is not None:
            if isinstance(budget_val, bool) or not isinstance(budget_val, int):
                raise MalformedPayloadError(
                    f"budget cap must be an integer, got {type(budget_val).__name__}"
                )
            if budget_val < 0:
                raise MalformedPayloadError("budget cap cannot be negative")
            budget_money = Money(minor_units=budget_val, currency=currency)
        else:
            budget_money = None

        mode_str = str(
            request.constraints.get("execution_mode", request.constraints.get("mode", "AUTONOMOUS"))
        ).upper()
        mode = (
            AuthorizationMode.AUTONOMOUS
            if mode_str == "AUTONOMOUS"
            else AuthorizationMode.SUPERVISED
        )

        action_id = (
            request.message_id
            if request.message_id.startswith("uap_")
            else f"uap_{request.message_id}"
        )

        action = ActionRequest(
            action_id=action_id,
            tenant_id=effective_tenant,
            principal_id=request.header.principal,
            agent_id=agent_id,
            domain=domain,
            action=action_name,
            resource=ResourceRef(type="product", id=resource_id),
            requested_at=request.timestamp,
            intent=Intent(
                purpose=str(p.get("purpose", f"UAP capability {request.capability}")),
                budget_max=budget_money,
            ),
            amount=amount,
            authorization_mode=mode,
        )

        metadata = {
            "variant_id": variant_id,
            "quantity": quantity,
            "idempotency_key": request.constraints.get("idempotency_key"),
        }
        return action, metadata

    def from_execution_result(
        self, request: UapMessage, result: PurchaseExecutionResult
    ) -> UapResponse:
        """Translate canonical PurchaseExecutionResult into UapResponse."""
        res_data = {
            "action_id": result.action_id,
            "decision": result.decision.value,
            "status": result.status.value,
            "order_id": result.order.order_id if result.order else None,
            "payment_id": result.payment.attempt_id if result.payment else None,
            "approval_id": result.approval.approval_id if result.approval else None,
        }

        err_obj: UapError | None = None
        if result.rejection_reason:
            err_obj = UapError(
                code="POLICY_REJECTION",
                message=result.rejection_reason,
                data={"status": result.status.value, "decision": result.decision.value},
            )

        return UapResponse(
            uap_version=request.uap_version,
            message_id=f"resp_{request.message_id}",
            reply_to=request.message_id,
            status="SUCCESS" if not err_obj else "REJECTED",
            result=res_data,
            error=err_obj,
            trace_id=request.trace_id,
        )


UAPAdapter = UapAdapter

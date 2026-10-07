"""Agent Commerce Protocol (ACP) adapter implementation (§107, §108).

Provides:
- Inbound translation of ACP shopping cart checkout payloads into canonical ActionRequests.
- Outbound translation of PurchaseExecutionResult into standardized ACP checkout responses.
- Typed schemas for ACP buyers, cart items, requests, and responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

SUPPORTED_ACP_VERSIONS = frozenset({"1.0", "acp/1.0", "2026-03"})


@dataclass(frozen=True, slots=True)
class AcpBuyer:
    """Buyer principal and agent metadata in ACP."""

    principal_id: str
    agent_id: str
    tenant_id: str
    delegation_token: str | None = None


@dataclass(frozen=True, slots=True)
class AcpCartItem:
    """Individual line item in an ACP shopping cart."""

    product_id: str
    variant_id: str
    sku: str
    name: str
    quantity: int
    unit_price_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class AcpPurchaseRequest:
    """Parsed and validated ACP checkout request."""

    acp_version: str
    request_id: str
    buyer: AcpBuyer
    items: tuple[AcpCartItem, ...]
    purpose: str
    timestamp: datetime
    budget_max_minor: int | None = None
    autonomous: bool = True
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class AcpPurchaseResponse:
    """Standardized ACP response payload."""

    acp_version: str
    request_id: str
    status: str
    decision: str
    order_id: str | None
    payment_id: str | None
    approval_id: str | None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary conforming to ACP specification."""
        return {
            "acp_version": self.acp_version,
            "request_id": self.request_id,
            "status": self.status,
            "decision": self.decision,
            "order_id": self.order_id,
            "payment_id": self.payment_id,
            "approval_id": self.approval_id,
            "rejection_reason": self.rejection_reason,
        }


# Schema Aliases
ACPBuyer = AcpBuyer
ACPItem = AcpCartItem
ACPCheckoutRequest = AcpPurchaseRequest
ACPResponse = AcpPurchaseResponse


class AcpAdapter(BaseProtocolAdapter[AcpPurchaseRequest, AcpPurchaseResponse]):
    """Adapter translating ACP messages to and from AgentPay canonical models."""

    def parse_payload(self, raw_data: dict[str, Any]) -> AcpPurchaseRequest:
        """Parse raw ACP dictionary payload into validated AcpPurchaseRequest."""
        if not isinstance(raw_data, dict):
            raise MalformedPayloadError("ACP payload must be a JSON object")

        version = str(raw_data.get("acp_version", raw_data.get("version", "")))
        if not version or version not in SUPPORTED_ACP_VERSIONS:
            raise UnsupportedProtocolVersionError(
                f"Unsupported ACP version '{version}'. Supported: {sorted(SUPPORTED_ACP_VERSIONS)}"
            )

        request_id = str(raw_data.get("request_id", raw_data.get("action_id", "")))
        if not request_id:
            raise MalformedPayloadError("Missing required field 'request_id'")

        buyer_data = raw_data.get("buyer")
        if not isinstance(buyer_data, dict):
            raise MalformedPayloadError("Missing or invalid 'buyer' block in ACP payload")

        for fld in ("principal_id", "agent_id", "tenant_id"):
            if not buyer_data.get(fld):
                raise MalformedPayloadError(f"Missing required buyer field '{fld}'")

        buyer = AcpBuyer(
            principal_id=str(buyer_data["principal_id"]),
            agent_id=str(buyer_data["agent_id"]),
            tenant_id=str(buyer_data["tenant_id"]),
            delegation_token=buyer_data.get("delegation_token"),
        )

        cart_data = raw_data.get("cart")
        raw_items: Any = (
            cart_data.get("items") if isinstance(cart_data, dict) else raw_data.get("items")
        )

        if not raw_items or not isinstance(raw_items, list):
            raise MalformedPayloadError("Missing or empty 'items' list in ACP cart")

        parsed_items: list[AcpCartItem] = []
        currencies: set[str] = set()

        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise MalformedPayloadError(f"Cart item #{idx} must be an object")

            product_id = item.get("product_id")
            if not product_id:
                raise MalformedPayloadError(f"Cart item #{idx} missing 'product_id'")

            try:
                qty = int(item.get("quantity", 1))
                if qty <= 0:
                    raise ValueError("quantity must be greater than zero")

                price = int(item["unit_price_minor"])
                if price < 0:
                    raise ValueError("unit_price_minor cannot be negative")

                curr = str(item.get("currency", "INR")).upper()
                if not curr:
                    raise ValueError("currency cannot be empty")
                currencies.add(curr)

                parsed_items.append(
                    AcpCartItem(
                        product_id=str(product_id),
                        variant_id=str(item.get("variant_id", product_id)),
                        sku=str(item.get("sku", product_id)),
                        name=str(item.get("name", "Product Item")),
                        quantity=qty,
                        unit_price_minor=price,
                        currency=curr,
                    )
                )
            except (KeyError, ValueError, TypeError) as err:
                raise MalformedPayloadError(f"Invalid cart item #{idx}: {err}") from err

        if len(currencies) > 1:
            raise MalformedPayloadError(f"Cart items contain multiple currencies: {currencies}")

        intent_data = raw_data.get("intent", {})
        purpose = str(
            intent_data.get("purpose")
            if isinstance(intent_data, dict) and intent_data.get("purpose")
            else raw_data.get("purpose", "ACP Purchase Checkout")
        )
        budget_max = (
            intent_data.get("budget_max_minor")
            if isinstance(intent_data, dict) and "budget_max_minor" in intent_data
            else raw_data.get("budget_max_minor")
        )

        exec_data = raw_data.get("execution", {})
        mode_str = str(
            exec_data.get("mode")
            if isinstance(exec_data, dict) and exec_data.get("mode")
            else raw_data.get("mode", "autonomous")
        ).lower()

        idempotency_key = (
            exec_data.get("idempotency_key")
            if isinstance(exec_data, dict) and exec_data.get("idempotency_key")
            else raw_data.get("idempotency_key")
        )

        return AcpPurchaseRequest(
            acp_version=version,
            request_id=request_id,
            buyer=buyer,
            items=tuple(parsed_items),
            purpose=purpose,
            timestamp=parse_iso_utc(raw_data.get("timestamp")),
            budget_max_minor=int(budget_max) if budget_max is not None else None,
            autonomous=(mode_str == "autonomous"),
            idempotency_key=str(idempotency_key) if idempotency_key else None,
        )

    def to_action_request(
        self, request: AcpPurchaseRequest, tenant_id: str | None = None
    ) -> tuple[ActionRequest, dict[str, Any]]:
        """Translate typed AcpPurchaseRequest into canonical ActionRequest and metadata."""
        effective_tenant = request.buyer.tenant_id or (tenant_id or "")
        first_item = request.items[0]
        currency = first_item.currency

        total_minor = sum(it.unit_price_minor * it.quantity for it in request.items)
        amount = Money(minor_units=total_minor, currency=currency)

        budget_money = (
            Money(minor_units=request.budget_max_minor, currency=currency)
            if request.budget_max_minor is not None
            else None
        )

        mode = AuthorizationMode.AUTONOMOUS if request.autonomous else AuthorizationMode.SUPERVISED

        action = ActionRequest(
            action_id=request.request_id,
            tenant_id=effective_tenant,
            principal_id=request.buyer.principal_id,
            agent_id=request.buyer.agent_id,
            domain="commerce",
            action="purchase",
            resource=ResourceRef(type="product", id=first_item.product_id),
            requested_at=request.timestamp,
            intent=Intent(purpose=request.purpose, budget_max=budget_money),
            amount=amount,
            authorization_mode=mode,
        )

        metadata = {
            "variant_id": first_item.variant_id,
            "quantity": first_item.quantity,
            "idempotency_key": request.idempotency_key,
        }
        return action, metadata

    def from_execution_result(
        self, request: AcpPurchaseRequest, result: PurchaseExecutionResult
    ) -> AcpPurchaseResponse:
        """Translate canonical PurchaseExecutionResult into AcpPurchaseResponse."""
        return AcpPurchaseResponse(
            acp_version=request.acp_version,
            request_id=request.request_id,
            status=result.status.value,
            decision=result.decision.value,
            order_id=result.order.order_id if result.order else None,
            payment_id=result.payment.attempt_id if result.payment else None,
            approval_id=result.approval.approval_id if result.approval else None,
            rejection_reason=result.rejection_reason,
        )


ACPAdapter = AcpAdapter

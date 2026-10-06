"""ASGI Application and HTTP API endpoints for AgentPay (§13, §21, §38, §67).

Provides:
- Standardized error envelopes for all failures (§13).
- Machine-readable agent endpoints (`/agent/...`, §38).
- Public versioned API endpoints (`/api/v1/...`).
- Emergency Kill Switch controls (§21, §67).
- Multi-tenant boundary enforcement on every request (§49, §50).
- Hermetic ASGI dispatch and test client for fast local testing without network services.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from agentpay.api.schemas import (
    ActionSubmissionRequest,
    ApiErrorDetail,
    ApiErrorResponse,
)
from agentpay.application.use_cases.purchase_flow import (
    PurchaseExecutionResult,
    PurchaseOrchestrator,
)
from agentpay.domain.action_request import (
    ActionRequest,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.errors import (
    CrossTenantAccessError,
    DomainError,
    InsufficientInventoryError,
    PrivilegeEscalationAttemptError,
)
from agentpay.domain.money import Money

Scope = dict[str, Any]
Receive = Callable[[], Coroutine[Any, Any, dict[str, Any]]]
Send = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class AgentPayApiApp:
    """Core HTTP Application exposing AgentPay control plane APIs."""

    def __init__(self, orchestrator: PurchaseOrchestrator) -> None:
        self.orchestrator = orchestrator
        self._emergency_paused: bool = False
        self._pause_metadata: dict[str, Any] = {}

    @property
    def is_emergency_paused(self) -> bool:
        return self._emergency_paused

    def pause_emergency(self, operator_id: str, reason: str, scope: str = "ALL") -> None:
        """Trigger emergency kill switch (§21, §67)."""
        self._emergency_paused = True
        self._pause_metadata = {
            "paused_by": operator_id,
            "reason": reason,
            "scope": scope,
            "paused_at": datetime.now(UTC).isoformat(),
        }

    def resume_emergency(self, operator_id: str) -> None:
        """Resume normal autonomous operations."""
        self._emergency_paused = False
        self._pause_metadata = {
            "resumed_by": operator_id,
            "resumed_at": datetime.now(UTC).isoformat(),
        }

    def get_emergency_status(self) -> dict[str, Any]:
        """Return current emergency kill switch status (§21, §67)."""
        return {
            "paused": self._emergency_paused,
            "metadata": dict(self._pause_metadata),
        }

    def _error_response(
        self,
        status_code: int,
        code: str,
        message: str,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        err = ApiErrorResponse(
            error=ApiErrorDetail(
                code=code,
                message=message,
                request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
                details=details or {},
            )
        )
        return status_code, err.to_dict()

    def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Synchronous dispatch for hermetic testing and clean request processing."""
        request_id = headers.get("x-request-id", f"req_{uuid.uuid4().hex[:12]}")
        tenant_id = headers.get("x-tenant-id", "")

        # 1. Health check
        if path == "/api/v1/health" and method == "GET":
            return 200, {
                "status": "healthy",
                "emergency_paused": self._emergency_paused,
                "version": "1.0.0-mvp",
            }

        # 2. OpenAPI Specification
        if path == "/openapi.json" and method == "GET":
            return 200, {
                "openapi": "3.1.0",
                "info": {"title": "AgentPay API", "version": "1.0.0"},
                "paths": {
                    "/api/v1/actions": {"post": {"summary": "Submit ActionRequest"}},
                    "/agent/catalog": {"get": {"summary": "Agent Catalog Discovery"}},
                    "/api/v1/emergency/pause": {"post": {"summary": "Kill Switch Pause"}},
                },
            }

        # Tenant header required for all tenant-scoped routes (§49, §50)
        if not tenant_id:
            return self._error_response(
                400, "MISSING_TENANT_HEADER", "X-Tenant-ID header is required", request_id
            )

        # 3. Emergency Status & Controls (§21, §67)
        if path == "/api/v1/emergency/status" and method == "GET":
            return 200, {
                "paused": self._emergency_paused,
                "metadata": self._pause_metadata,
            }

        if path == "/api/v1/emergency/pause" and method == "POST":
            req_data = body or {}
            operator_id = str(req_data.get("operator_id", "operator"))
            reason = str(req_data.get("reason", "Manual emergency pause"))
            self.pause_emergency(operator_id=operator_id, reason=reason)
            return 200, {"status": "PAUSED", "metadata": self._pause_metadata}

        if path == "/api/v1/emergency/resume" and method == "POST":
            req_data = body or {}
            operator_id = str(req_data.get("operator_id", "operator"))
            self.resume_emergency(operator_id=operator_id)
            return 200, {"status": "ACTIVE", "metadata": self._pause_metadata}

        # 4. Agent Catalog endpoints (§38)
        if path == "/agent/catalog" and method == "GET":
            products = []
            if self.orchestrator._product_repo is not None:
                products = self.orchestrator._product_repo.search(tenant_id, "")
            return 200, {
                "products": [
                    {
                        "product_id": p.product_id,
                        "name": p.name,
                        "category": p.category,
                        "merchant_id": p.merchant_id,
                    }
                    for p in products
                ]
            }

        if path.startswith("/agent/products/") and method == "GET":
            parts = path.strip("/").split("/")
            product_id = parts[2]
            if len(parts) == 3:
                # GET /agent/products/{id}
                prod = None
                if self.orchestrator._product_repo is not None:
                    prod = self.orchestrator._product_repo.get(tenant_id, product_id)
                if prod is None:
                    return self._error_response(
                        404, "PRODUCT_NOT_FOUND", f"Product '{product_id}' not found", request_id
                    )
                return 200, {
                    "product_id": prod.product_id,
                    "name": prod.name,
                    "category": prod.category,
                    "description": prod.description,
                }
            elif len(parts) == 4 and parts[3] == "inventory":
                # GET /agent/products/{id}/inventory
                inv = None
                if self.orchestrator._inventory_repo is not None:
                    inv = self.orchestrator._inventory_repo.get(tenant_id, product_id)
                return 200, {
                    "resource_id": product_id,
                    "available": inv.available if inv else 0,
                    "reserved": inv.reserved if inv else 0,
                    "effective_available": inv.effective_available if inv else 0,
                }

        # 5. Autonomous Action Execution (POST /api/v1/actions or POST /agent/actions)
        if (path in ("/api/v1/actions", "/agent/actions")) and method == "POST":
            if self._emergency_paused:
                return self._error_response(
                    503,
                    "EMERGENCY_PAUSE_ACTIVE",
                    "Autonomous payment processing is temporarily suspended by emergency control",
                    request_id,
                )

            req = body or {}
            try:
                sub = ActionSubmissionRequest(
                    action_id=req["action_id"],
                    principal_id=req["principal_id"],
                    agent_id=req["agent_id"],
                    domain=req.get("domain", "commerce"),
                    action=req.get("action", "purchase"),
                    resource_type=req.get("resource_type", "product"),
                    resource_id=req["resource_id"],
                    amount_minor=int(req["amount_minor"]),
                    currency=req.get("currency", "INR"),
                    intent_purpose=req.get("intent_purpose", "procurement"),
                    variant_id=req["variant_id"],
                    quantity=int(req.get("quantity", 1)),
                    idempotency_key=req.get("idempotency_key"),
                )
            except (KeyError, ValueError, TypeError) as err:
                return self._error_response(
                    422, "INVALID_REQUEST_BODY", f"Missing or invalid field: {err}", request_id
                )

            action = ActionRequest(
                action_id=sub.action_id,
                tenant_id=tenant_id,
                principal_id=sub.principal_id,
                agent_id=sub.agent_id,
                domain=sub.domain,
                action=sub.action,
                resource=ResourceRef(type=sub.resource_type, id=sub.resource_id),
                requested_at=datetime.now(UTC),
                intent=Intent(purpose=sub.intent_purpose),
                amount=Money(sub.amount_minor, sub.currency),
                authorization_mode=AuthorizationMode.AUTONOMOUS,
            )

            try:
                res: PurchaseExecutionResult = self.orchestrator.execute_purchase(
                    action=action,
                    tenant_id=tenant_id,
                    variant_id=sub.variant_id,
                    quantity=sub.quantity,
                    idempotency_key=sub.idempotency_key,
                )
            except CrossTenantAccessError as err:
                return self._error_response(403, "CROSS_TENANT_ACCESS_DENIED", str(err), request_id)
            except PrivilegeEscalationAttemptError as err:
                return self._error_response(
                    403, "PRIVILEGE_ESCALATION_DENIED", str(err), request_id
                )
            except InsufficientInventoryError as err:
                return self._error_response(409, "INSUFFICIENT_INVENTORY", str(err), request_id)
            except DomainError as err:
                return self._error_response(400, err.code, str(err), request_id)

            return 200, {
                "action_id": res.action_id,
                "status": res.status.value,
                "decision": res.decision.value,
                "order_id": res.order.order_id if res.order else None,
                "payment_id": res.payment.attempt_id if res.payment else None,
                "approval_id": res.approval.approval_id if res.approval else None,
                "rejection_reason": res.rejection_reason,
            }

        # 6. Approvals (POST /api/v1/approvals/{id}/approve or reject)
        if path.startswith("/api/v1/approvals/") and method == "POST":
            parts = path.strip("/").split("/")
            if len(parts) == 5:
                approval_id = parts[3]
                sub_action = parts[4]
                req = body or {}
                approver_id = req.get("approver_id", "")
                reason = req.get("reason", "")

                if not approver_id:
                    return self._error_response(
                        422, "MISSING_APPROVER_ID", "approver_id is required", request_id
                    )

                try:
                    if sub_action == "approve":
                        updated = self.orchestrator._approval_service.grant_approval(
                            approval_id=approval_id,
                            approver_id=approver_id,
                            tenant_id=tenant_id,
                        )
                    elif sub_action == "reject":
                        updated = self.orchestrator._approval_service.reject_approval(
                            approval_id=approval_id,
                            approver_id=approver_id,
                            reason=reason,
                            tenant_id=tenant_id,
                        )
                    else:
                        return self._error_response(
                            404, "NOT_FOUND", f"Unknown approval action '{sub_action}'", request_id
                        )
                except DomainError as err:
                    return self._error_response(400, err.code, str(err), request_id)

                return 200, {
                    "approval_id": updated.approval_id,
                    "status": updated.status.value,
                    "approver_id": updated.approver_id,
                }

        return self._error_response(
            404, "ENDPOINT_NOT_FOUND", f"Cannot {method} {path}", request_id
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 3.0 interface implementation."""
        if scope["type"] != "http":
            return

        method = scope["method"]
        path = scope["path"]
        raw_headers = scope.get("headers", [])
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in raw_headers}

        body_bytes = b""
        more_body = True
        while more_body:
            message = await receive()
            body_bytes += message.get("body", b"")
            more_body = message.get("more_body", False)

        body_json = None
        if body_bytes:
            try:
                body_json = json.loads(body_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                body_json = {}

        status_code, resp_data = self.handle_request(method, path, headers, body_json)
        resp_bytes = json.dumps(resp_data).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(resp_bytes)).encode("latin1")],
                ],
            }
        )
        await send({"type": "http.response.body", "body": resp_bytes})


class ApiTestClient:
    """Fast, synchronous client for testing the AgentPay HTTP API without network calls."""

    def __init__(self, app: AgentPayApiApp) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        return self.app.handle_request(method, path, hdrs, json_body)

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return self.request("POST", path, headers=headers, json_body=json_body)

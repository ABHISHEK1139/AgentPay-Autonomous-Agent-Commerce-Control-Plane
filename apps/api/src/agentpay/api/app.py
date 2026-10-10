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
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from agentpay.adapters.in_memory.approval_repository import InMemoryApprovalRepository
from agentpay.adapters.in_memory.identity_repository import InMemoryCredentialRepository
from agentpay.adapters.protocols import (
    AcpAdapter,
    Ap2Adapter,
    MalformedPayloadError,
    ProtocolError,
    UapAdapter,
    UnsupportedActionError,
    UnsupportedProtocolVersionError,
    X402Adapter,
)
from agentpay.api.schemas import (
    ActionSubmissionRequest,
    ApiErrorDetail,
    ApiErrorResponse,
)
from agentpay.application.ports.identity_port import CredentialRepository
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
    IdempotencyConflictError,
    InsufficientInventoryError,
    PrivilegeEscalationAttemptError,
    UnauthorizedToolAccessError,
    UnknownToolError,
)
from agentpay.domain.identity import (
    Credential,
    CredentialStatus,
    CredentialType,
    UserRole,
)
from agentpay.domain.money import Money

Scope = dict[str, Any]
Receive = Callable[[], Coroutine[Any, Any, dict[str, Any]]]
Send = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

_current_trace_id: ContextVar[str | None] = ContextVar("_current_trace_id", default=None)


class AgentPayApiApp:
    """Core HTTP Application exposing AgentPay control plane APIs."""

    def __init__(
        self,
        orchestrator: PurchaseOrchestrator,
        credential_repo: CredentialRepository | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.credential_repo: CredentialRepository = (
            credential_repo if credential_repo is not None else InMemoryCredentialRepository()
        )
        self._emergency_paused: bool = False
        self._pause_metadata: dict[str, Any] = {}
        self.acp_adapter = AcpAdapter()
        self.ap2_adapter = Ap2Adapter()
        self.uap_adapter = UapAdapter()
        self.x402_adapter = X402Adapter()

    def _extract_protocol_tenant(self, path: str, body: dict[str, Any] | None) -> str:
        """Extract tenant context from protocol payload when header is omitted."""
        if not body or not isinstance(body, dict):
            return ""
        if "/acp/" in path:
            buyer = body.get("buyer")
            if isinstance(buyer, dict) and buyer.get("tenant_id"):
                return str(buyer["tenant_id"])
        elif "/ap2/" in path:
            payer = body.get("payer")
            if isinstance(payer, dict) and payer.get("tenant_id"):
                return str(payer["tenant_id"])
        elif "/uap/" in path:
            hdr = body.get("header")
            if isinstance(hdr, dict) and hdr.get("tenant"):
                return str(hdr["tenant"])
        elif "/x402/" in path:
            if body.get("tenant_id"):
                return str(body["tenant_id"])
        if body.get("tenant_id"):
            return str(body["tenant_id"])
        return ""

    def _ensure_resource_inventory(
        self, tenant_id: str, variant_id: str, action: ActionRequest | None = None
    ) -> None:
        """Ensure virtual inventory, variant, and product exist for digital resources and non-catalog payment mandates."""
        if not variant_id:
            return

        from agentpay.domain.commerce import Inventory, Product, ProductVariant
        from agentpay.domain.money import Money

        if (
            self.orchestrator._variant_repo is not None
            and self.orchestrator._variant_repo.get(tenant_id, variant_id) is None
        ):
            price = action.amount if (action and action.amount) else Money(1000, "INR")
            prod_id = f"prod_{variant_id}"

            default_merchant_id = "merch_01"
            if self.orchestrator._merchant_repo is not None:
                for candidate in ["merch_prime", "merch_01", "merch_acme"]:
                    m = self.orchestrator._merchant_repo.get(tenant_id, candidate)
                    if m is not None and m.is_active:
                        default_merchant_id = m.merchant_id
                        break

            category = "electronics"
            if self.orchestrator._permissions_repo is not None and action is not None:
                perms = self.orchestrator._permissions_repo.get(tenant_id, action.agent_id)
                if perms is not None and perms.allowed_categories:
                    category = perms.allowed_categories[0]

            if (
                self.orchestrator._product_repo is not None
                and self.orchestrator._product_repo.get(tenant_id, prod_id) is None
            ):
                self.orchestrator._product_repo.save(
                    tenant_id,
                    Product(
                        product_id=prod_id,
                        tenant_id=tenant_id,
                        merchant_id=default_merchant_id,
                        name=f"Digital Resource {variant_id}",
                        category=category,
                        description="Auto-provisioned protocol resource",
                    ),
                )

            self.orchestrator._variant_repo.save(
                tenant_id,
                ProductVariant(
                    variant_id=variant_id,
                    product_id=prod_id,
                    sku=f"SKU-{variant_id}",
                    name=f"Digital Resource {variant_id}",
                    price=price,
                ),
            )

        if (
            self.orchestrator._inventory_repo is not None
            and self.orchestrator._inventory_repo.get(tenant_id, variant_id) is None
        ):
            self.orchestrator._inventory_repo.save(
                tenant_id,
                Inventory(
                    variant_id=variant_id,
                    tenant_id=tenant_id,
                    available=999999,
                    reserved=0,
                ),
            )

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
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        effective_trace_id = trace_id or _current_trace_id.get()
        err = ApiErrorResponse(
            error=ApiErrorDetail(
                code=code,
                message=message,
                request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
                details=details or {},
                trace_id=effective_trace_id,
            )
        )
        return status_code, err.to_dict()

    def _authenticate_request(
        self, headers: dict[str, str], request_id: str
    ) -> tuple[Credential | None, tuple[int, dict[str, Any]] | None]:
        """Extract and verify caller credentials from Authorization or X-API-Key headers (§14)."""
        auth_header = headers.get("authorization", "").strip()
        api_key_header = headers.get("x-api-key", "").strip()

        raw_secret: str | None = None
        if auth_header:
            lower_auth = auth_header.lower()
            if lower_auth.startswith("bearer ") or lower_auth.startswith("apikey "):
                raw_secret = auth_header[7:].strip()
            else:
                return None, self._error_response(
                    401,
                    "UNAUTHENTICATED",
                    "Unsupported authorization scheme (expected Bearer or ApiKey)",
                    request_id,
                )
        elif api_key_header:
            raw_secret = api_key_header

        if not raw_secret:
            return None, self._error_response(
                401,
                "UNAUTHENTICATED",
                "Authentication credentials required via 'Authorization: Bearer <token>' or 'X-API-Key: <key>'",
                request_id,
            )

        secret_hash = Credential.hash_secret(raw_secret)
        cred = self.credential_repo.get_by_secret_hash(secret_hash)
        if cred is None:
            return None, self._error_response(
                401,
                "UNAUTHENTICATED",
                "Invalid authentication credentials",
                request_id,
            )

        if cred.status is CredentialStatus.REVOKED:
            return None, self._error_response(
                401,
                "UNAUTHENTICATED",
                "Credential has been revoked",
                request_id,
            )

        if cred.is_expired() or cred.status is CredentialStatus.EXPIRED:
            return None, self._error_response(
                401,
                "UNAUTHENTICATED",
                "Credential has expired",
                request_id,
            )

        if not cred.is_active:
            return None, self._error_response(
                401,
                "UNAUTHENTICATED",
                "Credential is not active",
                request_id,
            )

        return cred, None

    def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Synchronous dispatch for hermetic testing and clean request processing."""
        hdrs = {k.lower(): v for k, v in headers.items()}
        request_id = hdrs.get("x-request-id", f"req_{uuid.uuid4().hex[:12]}")
        trace_id = (
            hdrs.get("x-trace-id") or hdrs.get("traceparent") or f"trace_{uuid.uuid4().hex[:16]}"
        )
        tenant_id = hdrs.get("x-tenant-id", "")

        ctx_token = _current_trace_id.set(trace_id)
        try:
            return self._dispatch(method, path, hdrs, body, request_id, tenant_id)
        except UnknownToolError as err:
            return self._error_response(400, "UNKNOWN_TOOL", str(err), request_id)
        except UnauthorizedToolAccessError as err:
            return self._error_response(403, "UNAUTHORIZED_TOOL_ACCESS", str(err), request_id)
        except Exception:
            return self._error_response(
                500,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred",
                request_id,
                trace_id=trace_id,
            )
        finally:
            _current_trace_id.reset(ctx_token)

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        request_id: str,
        tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:

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
                    "/api/v1/tools/{tool_name}/invoke": {
                        "post": {"summary": "Invoke Registered Tool"}
                    },
                    "/agent/tools/{tool_name}/execute": {
                        "post": {"summary": "Execute Registered Tool"}
                    },
                    "/api/v1/protocols/acp/checkout": {"post": {"summary": "ACP Checkout"}},
                    "/api/v1/protocols/ap2/mandates": {
                        "post": {"summary": "AP2 Mandate Settlement"}
                    },
                    "/api/v1/protocols/uap/rpc": {"post": {"summary": "UAP JSON-RPC Invocation"}},
                    "/api/v1/protocols/x402/pay": {
                        "post": {"summary": "x402 Challenge Settlement"}
                    },
                },
            }

        # --- AUTHENTICATION ENFORCEMENT (§14) ---
        cred, auth_err = self._authenticate_request(headers, request_id)
        if auth_err is not None or cred is None:
            return (
                auth_err
                if auth_err is not None
                else self._error_response(
                    401, "UNAUTHENTICATED", "Authentication required", request_id
                )
            )

        # 3. Emergency Status & Controls (§21, §67)
        # Platform-level operator controls operate cluster-wide; require AGENT_OPERATOR or ADMIN role
        if path.startswith("/api/v1/emergency/"):
            if not cred.is_operator_or_admin():
                return self._error_response(
                    403,
                    "UNAUTHORIZED_OPERATOR",
                    "Emergency control routes require AGENT_OPERATOR or ADMIN role",
                    request_id,
                    details={
                        "principal_id": cred.principal_id,
                        "role": cred.role.value if cred.role else None,
                        "required_roles": ["ADMIN", "AGENT_OPERATOR"],
                    },
                )

            if path == "/api/v1/emergency/status" and method == "GET":
                return 200, {
                    "paused": self._emergency_paused,
                    "emergency_paused": self._emergency_paused,
                    "metadata": self._pause_metadata,
                }

            if path == "/api/v1/emergency/pause" and method == "POST":
                req_data = body or {}
                operator_id = str(req_data.get("operator_id") or cred.principal_id)
                reason = str(req_data.get("reason", "Manual emergency pause"))
                scope = str(req_data.get("scope", "ALL"))
                self.pause_emergency(operator_id=operator_id, reason=reason, scope=scope)
                return 200, {
                    "status": "PAUSED",
                    "paused": True,
                    "emergency_paused": True,
                    "metadata": self._pause_metadata,
                }

            if path == "/api/v1/emergency/resume" and method == "POST":
                req_data = body or {}
                operator_id = str(req_data.get("operator_id") or cred.principal_id)
                self.resume_emergency(operator_id=operator_id)
                return 200, {
                    "status": "ACTIVE",
                    "paused": False,
                    "emergency_paused": False,
                    "metadata": self._pause_metadata,
                }

            return self._error_response(
                404, "ENDPOINT_NOT_FOUND", f"Cannot {method} {path}", request_id
            )

        # --- TENANT BINDING ENFORCEMENT FOR TENANT-SCOPED ROUTES (§49, §50) ---
        if not tenant_id and path.startswith("/api/v1/protocols/"):
            tenant_id = self._extract_protocol_tenant(path, body)

        if not tenant_id:
            return self._error_response(
                400, "MISSING_TENANT_HEADER", "X-Tenant-ID header is required", request_id
            )

        if tenant_id != cred.tenant_id:
            return self._error_response(
                403,
                "CROSS_TENANT_ACCESS_DENIED",
                f"Asserted tenant '{tenant_id}' does not match authenticated credential tenant '{cred.tenant_id}'",
                request_id,
                details={
                    "asserted_tenant_id": tenant_id,
                    "credential_tenant_id": cred.tenant_id,
                },
            )

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
                    "variant_id": product_id,
                    "available": inv.available if inv else 0,
                    "reserved": inv.reserved if inv else 0,
                    "effective_available": inv.effective_available if inv else 0,
                }

        # 4b. Tool Execution endpoints (§26, §27)
        is_v1_tool = path.startswith("/api/v1/tools/") and method == "POST"
        is_agent_tool = path.startswith("/agent/tools/") and method == "POST"
        if is_v1_tool or is_agent_tool:
            parts = path.strip("/").split("/")
            tool_name = ""
            if is_v1_tool and len(parts) >= 4:
                tool_name = parts[3]
            elif is_agent_tool and len(parts) >= 3:
                tool_name = parts[2]

            if not tool_name:
                return self._error_response(
                    400, "MISSING_TOOL_NAME", "Tool name is required in request path", request_id
                )

            req_data = body or {}
            agent_id = str(req_data.get("agent_id") or cred.principal_id)
            parameters = req_data.get("parameters") or req_data.get("params") or {}
            if not isinstance(parameters, dict):
                parameters = {}

            try:
                res_dict = self.orchestrator.execute_tool(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    parameters=parameters,
                )
                return 200, res_dict
            except UnknownToolError as err:
                return self._error_response(400, "UNKNOWN_TOOL", str(err), request_id)
            except UnauthorizedToolAccessError as err:
                return self._error_response(403, "UNAUTHORIZED_TOOL_ACCESS", str(err), request_id)
            except CrossTenantAccessError as err:
                return self._error_response(403, "CROSS_TENANT_ACCESS_DENIED", str(err), request_id)
            except PrivilegeEscalationAttemptError as err:
                return self._error_response(
                    403, "PRIVILEGE_ESCALATION_DENIED", str(err), request_id
                )
            except DomainError as err:
                return self._error_response(400, err.code, str(err), request_id)

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
            except IdempotencyConflictError as err:
                return self._error_response(409, "IDEMPOTENCY_CONFLICT", str(err), request_id)
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

        # 6. Approvals (GET /api/v1/approvals or POST /api/v1/approvals/{id}/approve or reject or grant)
        if path == "/api/v1/approvals" and method == "GET":
            pending_list: list[dict[str, Any]] = []
            if self.orchestrator._approval_repo is not None:
                repo = self.orchestrator._approval_repo
                if isinstance(repo, InMemoryApprovalRepository):
                    tenant_store = repo._approvals.get(tenant_id, {})
                    for a in tenant_store.values():
                        if a.status.value == "PENDING":
                            pending_list.append(
                                {
                                    "approval_id": a.approval_id,
                                    "action_id": a.binding.action_id,
                                    "status": a.status.value,
                                    "amount_minor": a.binding.amount.minor_units,
                                    "currency": a.binding.amount.currency,
                                }
                            )
            return 200, {"approvals": pending_list}

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
                    if sub_action in ("approve", "grant"):
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

        # 7. Protocol Adapter Endpoints (§107, §108)
        if (
            path in ("/api/v1/protocols/acp/checkout", "/api/v1/protocols/acp/actions")
            and method == "POST"
        ):
            if self._emergency_paused:
                return self._error_response(
                    503,
                    "EMERGENCY_PAUSE_ACTIVE",
                    "Autonomous payment processing is temporarily suspended by emergency control",
                    request_id,
                )
            try:
                acp_req = self.acp_adapter.parse_payload(body or {})
                if tenant_id and acp_req.buyer.tenant_id and tenant_id != acp_req.buyer.tenant_id:
                    return self._error_response(
                        403,
                        "CROSS_TENANT_ACCESS_DENIED",
                        "Header tenant does not match buyer tenant",
                        request_id,
                    )
                action, metadata = self.acp_adapter.to_action_request(acp_req, tenant_id=tenant_id)
            except MalformedPayloadError as err:
                return self._error_response(422, err.code, str(err), request_id, err.details)
            except UnsupportedProtocolVersionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except UnsupportedActionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except ProtocolError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except DomainError as err:
                return self._error_response(422, err.code, str(err), request_id)

            try:
                res = self.orchestrator.execute_purchase(
                    action=action,
                    tenant_id=tenant_id,
                    variant_id=metadata.get("variant_id", ""),
                    quantity=metadata.get("quantity", 1),
                    idempotency_key=metadata.get("idempotency_key"),
                )
            except CrossTenantAccessError as err:
                return self._error_response(403, "CROSS_TENANT_ACCESS_DENIED", str(err), request_id)
            except PrivilegeEscalationAttemptError as err:
                return self._error_response(
                    403, "PRIVILEGE_ESCALATION_DENIED", str(err), request_id
                )
            except InsufficientInventoryError as err:
                return self._error_response(409, "INSUFFICIENT_INVENTORY", str(err), request_id)
            except IdempotencyConflictError as err:
                return self._error_response(409, "IDEMPOTENCY_CONFLICT", str(err), request_id)
            except DomainError as err:
                return self._error_response(400, err.code, str(err), request_id)

            acp_resp = self.acp_adapter.from_execution_result(acp_req, res)
            return 200, acp_resp.to_dict()

        if (
            path in ("/api/v1/protocols/ap2/mandates", "/api/v1/protocols/ap2/payments")
            and method == "POST"
        ):
            if self._emergency_paused:
                return self._error_response(
                    503,
                    "EMERGENCY_PAUSE_ACTIVE",
                    "Autonomous payment processing is temporarily suspended by emergency control",
                    request_id,
                )
            try:
                ap2_req = self.ap2_adapter.parse_payload(body or {})
                if tenant_id and ap2_req.payer.tenant_id and tenant_id != ap2_req.payer.tenant_id:
                    return self._error_response(
                        403,
                        "CROSS_TENANT_ACCESS_DENIED",
                        "Header tenant does not match payer tenant",
                        request_id,
                    )
                action, metadata = self.ap2_adapter.to_action_request(ap2_req, tenant_id=tenant_id)
            except MalformedPayloadError as err:
                return self._error_response(422, err.code, str(err), request_id, err.details)
            except UnsupportedProtocolVersionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except UnsupportedActionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except ProtocolError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except DomainError as err:
                return self._error_response(422, err.code, str(err), request_id)

            try:
                v_id = metadata.get("variant_id", "")
                self._ensure_resource_inventory(tenant_id, v_id, action)
                res = self.orchestrator.execute_purchase(
                    action=action,
                    tenant_id=tenant_id,
                    variant_id=v_id,
                    quantity=metadata.get("quantity", 1),
                    idempotency_key=metadata.get("idempotency_key"),
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

            ap2_resp = self.ap2_adapter.from_execution_result(ap2_req, res)
            return 200, ap2_resp.to_dict()

        if (
            path in ("/api/v1/protocols/uap/rpc", "/api/v1/protocols/uap/messages")
            and method == "POST"
        ):
            if self._emergency_paused:
                return self._error_response(
                    503,
                    "EMERGENCY_PAUSE_ACTIVE",
                    "Autonomous payment processing is temporarily suspended by emergency control",
                    request_id,
                )
            try:
                uap_req = self.uap_adapter.parse_payload(body or {})
                if tenant_id and uap_req.header.tenant and tenant_id != uap_req.header.tenant:
                    return self._error_response(
                        403,
                        "CROSS_TENANT_ACCESS_DENIED",
                        "Header tenant does not match message tenant",
                        request_id,
                    )
                action, metadata = self.uap_adapter.to_action_request(uap_req, tenant_id=tenant_id)
            except MalformedPayloadError as err:
                return self._error_response(422, err.code, str(err), request_id, err.details)
            except UnsupportedProtocolVersionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except UnsupportedActionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except ProtocolError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except DomainError as err:
                return self._error_response(422, err.code, str(err), request_id)

            try:
                v_id = metadata.get("variant_id", "")
                self._ensure_resource_inventory(tenant_id, v_id, action)
                res = self.orchestrator.execute_purchase(
                    action=action,
                    tenant_id=tenant_id,
                    variant_id=v_id,
                    quantity=metadata.get("quantity", 1),
                    idempotency_key=metadata.get("idempotency_key"),
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

            uap_resp = self.uap_adapter.from_execution_result(uap_req, res)
            return 200, uap_resp.to_dict()

        if (
            path in ("/api/v1/protocols/x402/pay", "/api/v1/protocols/x402/challenge")
            and method == "POST"
        ):
            if self._emergency_paused:
                return self._error_response(
                    503,
                    "EMERGENCY_PAUSE_ACTIVE",
                    "Autonomous payment processing is temporarily suspended by emergency control",
                    request_id,
                )
            try:
                x402_req = self.x402_adapter.parse_payload(body or {})
                if tenant_id and x402_req.tenant_id and tenant_id != x402_req.tenant_id:
                    return self._error_response(
                        403,
                        "CROSS_TENANT_ACCESS_DENIED",
                        "Header tenant does not match payload tenant",
                        request_id,
                    )
                action, metadata = self.x402_adapter.to_action_request(
                    x402_req, tenant_id=tenant_id
                )
            except MalformedPayloadError as err:
                return self._error_response(422, err.code, str(err), request_id, err.details)
            except UnsupportedProtocolVersionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except UnsupportedActionError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except ProtocolError as err:
                return self._error_response(400, err.code, str(err), request_id, err.details)
            except DomainError as err:
                return self._error_response(422, err.code, str(err), request_id)

            try:
                v_id = metadata.get("variant_id", "")
                self._ensure_resource_inventory(tenant_id, v_id, action)
                res = self.orchestrator.execute_purchase(
                    action=action,
                    tenant_id=tenant_id,
                    variant_id=v_id,
                    quantity=metadata.get("quantity", 1),
                    idempotency_key=metadata.get("idempotency_key"),
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

            x402_resp = self.x402_adapter.from_execution_result(x402_req, res)
            return 200, x402_resp.to_dict()

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
            except (json.JSONDecodeError, UnicodeDecodeError):
                req_id = headers.get("x-request-id", f"req_{uuid.uuid4().hex[:12]}")
                status_code, resp_data = self._error_response(
                    422,
                    "INVALID_REQUEST_BODY",
                    "Request body must be valid JSON",
                    req_id,
                )
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
                return

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
    """Fast, synchronous client for testing the AgentPay HTTP API with automated test authentication."""

    def __init__(
        self,
        app: AgentPayApiApp,
        default_tenant: str = "tenant_01",
        default_role: UserRole = UserRole.ADMIN,
    ) -> None:
        self.app = app
        self.default_tenant = default_tenant
        self.default_role = default_role
        self._tokens_by_tenant: dict[str, str] = {}
        self._api_keys_by_tenant: dict[str, str] = {}

    def create_token(
        self,
        tenant_id: str = "tenant_01",
        principal_id: str = "usr_test_operator",
        role: UserRole | None = UserRole.ADMIN,
        status: CredentialStatus = CredentialStatus.ACTIVE,
        expires_at: datetime | None = None,
        scopes: tuple[str, ...] = ("admin", "operator", "commerce:read", "commerce:write"),
        raw_token: str | None = None,
    ) -> str:
        """Create and store a Bearer token credential in the app's credential repository."""
        token = raw_token or f"test_bearer_{tenant_id}_{uuid.uuid4().hex[:8]}"
        secret_hash = Credential.hash_secret(token)
        cred_id = f"cred_{uuid.uuid4().hex[:8]}"
        exp = expires_at or datetime(2099, 1, 1, tzinfo=UTC)

        cred = Credential(
            credential_id=cred_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            credential_type=CredentialType.BEARER_TOKEN,
            secret_hash=secret_hash,
            expires_at=exp,
            status=status,
            role=role,
            scopes=scopes,
        )
        if hasattr(self.app, "credential_repo") and self.app.credential_repo is not None:
            self.app.credential_repo.save(tenant_id, cred)
        return token

    def create_api_key(
        self,
        tenant_id: str = "tenant_01",
        principal_id: str = "usr_test_operator",
        role: UserRole | None = UserRole.ADMIN,
        status: CredentialStatus = CredentialStatus.ACTIVE,
        expires_at: datetime | None = None,
        scopes: tuple[str, ...] = ("admin", "operator", "commerce:read", "commerce:write"),
        raw_key: str | None = None,
    ) -> str:
        """Create and store an API key credential in the app's credential repository."""
        key = raw_key or f"apk_{tenant_id}_{uuid.uuid4().hex[:12]}"
        secret_hash = Credential.hash_secret(key)
        cred_id = f"cred_apk_{uuid.uuid4().hex[:8]}"
        exp = expires_at or datetime(2099, 1, 1, tzinfo=UTC)

        cred = Credential(
            credential_id=cred_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            credential_type=CredentialType.API_KEY,
            secret_hash=secret_hash,
            expires_at=exp,
            status=status,
            role=role,
            scopes=scopes,
        )
        if hasattr(self.app, "credential_repo") and self.app.credential_repo is not None:
            self.app.credential_repo.save(tenant_id, cred)
        return key

    def get_or_create_token(self, tenant_id: str, role: UserRole | None = UserRole.ADMIN) -> str:
        """Retrieve cached token or provision a new one for the given tenant and role."""
        role_key = role.value if role is not None else "none"
        key = f"{tenant_id}:{role_key}"
        if key not in self._tokens_by_tenant:
            self._tokens_by_tenant[key] = self.create_token(tenant_id=tenant_id, role=role)
        return self._tokens_by_tenant[key]

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}

        # Auto-inject default authentication unless explicitly disabled or already provided
        if (
            auth
            and "authorization" not in hdrs
            and "x-api-key" not in hdrs
            and path not in ("/api/v1/health", "/openapi.json")
        ):
            target_tenant = (
                hdrs.get("x-tenant-id")
                or self.app._extract_protocol_tenant(path, json_body)
                or self.default_tenant
            )
            token = self.get_or_create_token(tenant_id=target_tenant, role=self.default_role)
            hdrs["authorization"] = f"Bearer {token}"

        return self.app.handle_request(method, path, hdrs, json_body)

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        return self.request("GET", path, headers=headers, auth=auth)

    def post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        return self.request("POST", path, headers=headers, json_body=json_body, auth=auth)

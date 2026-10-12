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

import hashlib
import hmac
import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from agentpay.adapters.in_memory.emergency_pause_store import (
    InMemoryEmergencyPauseStore,
)
from agentpay.adapters.in_memory.identity_repository import InMemoryCredentialRepository
from agentpay.adapters.in_memory.payment_repository import (
    InMemoryIdempotencyStore,
    InMemoryPaymentRepository,
    InMemoryProviderTransactionMappingRepository,
    InMemoryProviderWebhookEventRepository,
)
from agentpay.adapters.in_memory.rate_limiter import InMemoryRateLimiter
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
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
from agentpay.application.ports.emergency_pause_port import EmergencyPauseStore
from agentpay.application.ports.identity_port import CredentialRepository
from agentpay.application.ports.payment_port import (
    IdempotencyStore,
    ProviderTransactionMappingRepository,
    ProviderWebhookEventRepository,
)
from agentpay.application.ports.rate_limit_port import RateLimiter
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.application.use_cases.purchase_flow import (
    PurchaseExecutionResult,
    PurchaseOrchestrator,
)
from agentpay.config.config import AppConfig
from agentpay.domain.action_request import (
    ActionRequest,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.approval import ApprovalAlreadyConsumedError
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    DomainError,
    IdempotencyConflictError,
    InsufficientInventoryError,
    PrivilegeEscalationAttemptError,
    RateLimitExceededError,
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
from agentpay.domain.payments import (
    IdempotencyRecord,
)

Scope = dict[str, Any]
Receive = Callable[[], Coroutine[Any, Any, dict[str, Any]]]
Send = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

_current_trace_id: ContextVar[str | None] = ContextVar("_current_trace_id", default=None)
MAX_REQUEST_BODY_BYTES: int = 1024 * 1024  # 1 MiB payload limit (§62, B-SRV-01)


class ApiResponse(tuple[Any, ...]):
    """HTTP response 2-tuple (status_code, body) compatible with tuple unpacking, with .headers attribute."""

    _headers: dict[str, str]

    def __new__(
        cls,
        status_code: int,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        inst = super().__new__(cls, (status_code, body))
        inst._headers = {k.lower(): str(v) for k, v in (headers or {}).items()}
        return inst

    @property
    def status_code(self) -> int:
        return self[0]  # type: ignore[no-any-return]

    @property
    def body(self) -> dict[str, Any]:
        return self[1]  # type: ignore[no-any-return]

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def as_tuple(self) -> tuple[int, dict[str, Any], dict[str, str]]:
        return (self[0], self[1], dict(self._headers))


class AgentPayApiApp:
    """Core HTTP Application exposing AgentPay control plane APIs."""

    def __init__(
        self,
        orchestrator: PurchaseOrchestrator,
        credential_repo: CredentialRepository | None = None,
        rate_limiter: RateLimiter | None = None,
        mutating_agent_capacity: int = 10,
        mutating_agent_refill_rate: float = 2.0,
        mutating_tenant_capacity: int = 50,
        mutating_tenant_refill_rate: float = 10.0,
        read_agent_capacity: int = 100,
        read_agent_refill_rate: float = 20.0,
        read_tenant_capacity: int = 500,
        read_tenant_refill_rate: float = 100.0,
        webhook_secret: str = "whsec_test_secret_32bytes_minimum_length",
        razorpay_webhook_secret: str | None = None,
        stripe_webhook_secret: str | None = None,
        reconciler: PaymentReconciler | None = None,
        idempotency_store: IdempotencyStore | None = None,
        provider_mapping_repo: ProviderTransactionMappingRepository | None = None,
        webhook_event_repo: ProviderWebhookEventRepository | None = None,
        require_observability_auth: bool = False,
        config: AppConfig | None = None,
        emergency_pause_store: EmergencyPauseStore | None = None,
    ) -> None:
        self.config: AppConfig | None = config
        self.emergency_pause_store: EmergencyPauseStore = (
            emergency_pause_store
            if emergency_pause_store is not None
            else InMemoryEmergencyPauseStore()
        )
        self.orchestrator = orchestrator
        self.credential_repo: CredentialRepository = (
            credential_repo if credential_repo is not None else InMemoryCredentialRepository()
        )
        self.rate_limiter: RateLimiter = (
            rate_limiter if rate_limiter is not None else InMemoryRateLimiter()
        )
        self._mutating_agent_capacity = mutating_agent_capacity
        self._mutating_agent_refill_rate = mutating_agent_refill_rate
        self._mutating_tenant_capacity = mutating_tenant_capacity
        self._mutating_tenant_refill_rate = mutating_tenant_refill_rate
        self._read_agent_capacity = read_agent_capacity
        self._read_agent_refill_rate = read_agent_refill_rate
        self._read_tenant_capacity = read_tenant_capacity
        self._read_tenant_refill_rate = read_tenant_refill_rate
        self.webhook_secret: str = webhook_secret

        env_req_obs = os.environ.get("AGENTPAY_REQUIRE_OBSERVABILITY_AUTH", "").lower() in (
            "true",
            "1",
            "yes",
        )
        self.require_observability_auth: bool = require_observability_auth or env_req_obs

        if idempotency_store is not None:
            self.idempotency_store = idempotency_store
        elif orchestrator._idempotency_store is not None:
            store = orchestrator._idempotency_store
            if not isinstance(store, IdempotencyStore):
                raise TypeError(f"Expected IdempotencyStore, got {type(store).__name__}")
            self.idempotency_store = store
        else:
            self.idempotency_store = InMemoryIdempotencyStore()

        mapping_repo = (
            provider_mapping_repo
            or getattr(orchestrator, "_provider_mapping_repo", None)
            or InMemoryProviderTransactionMappingRepository()
        )
        wb_repo = (
            webhook_event_repo
            or getattr(orchestrator, "_webhook_event_repo", None)
            or InMemoryProviderWebhookEventRepository()
        )
        self.webhook_event_repo: ProviderWebhookEventRepository = wb_repo

        if reconciler is not None:
            self.reconciler = reconciler
            if getattr(self.reconciler, "_provider_mapping_repo", None) is None:
                self.reconciler._provider_mapping_repo = mapping_repo
            if getattr(self.reconciler, "_webhook_event_repo", None) is None:
                self.reconciler._webhook_event_repo = wb_repo
        else:
            provider = getattr(orchestrator, "_payment_provider", None) or MockPaymentProvider()
            pay_repo = getattr(orchestrator, "_payment_repo", None) or InMemoryPaymentRepository()
            self.reconciler = PaymentReconciler(
                payment_provider=provider,
                payment_repo=pay_repo,
                order_repo=getattr(orchestrator, "_order_repo", None),
                inventory_repo=getattr(orchestrator, "_inventory_repo", None),
                audit_ledger=getattr(orchestrator, "_audit_ledger", None),
                provider_mapping_repo=mapping_repo,
                webhook_event_repo=wb_repo,
            )

        self.provider_mapping_repo = mapping_repo
        self.razorpay_webhook_secret: str = (
            razorpay_webhook_secret
            or os.environ.get("RAZORPAY_WEBHOOK_SECRET")
            or os.environ.get("AGENTPAY_WEBHOOK_SECRET")
            or getattr(getattr(self.reconciler, "_provider", None), "webhook_secret", None)
            or webhook_secret
        )
        self.stripe_webhook_secret: str = (
            stripe_webhook_secret
            or os.environ.get("STRIPE_WEBHOOK_SECRET")
            or os.environ.get("AGENTPAY_WEBHOOK_SECRET")
            or getattr(getattr(self.reconciler, "_provider", None), "webhook_secret", None)
            or webhook_secret
        )

        self._last_response_headers: dict[str, str] = {}
        self._emergency_paused: bool = False
        self._paused_scopes: set[str] = set()
        self._pause_metadata: dict[str, Any] = {}
        self._webhook_lock: threading.Lock = threading.Lock()
        self._processed_webhook_events: set[str] = set()
        self.acp_adapter = AcpAdapter()
        self.ap2_adapter = Ap2Adapter()
        self.uap_adapter = UapAdapter()
        self.x402_adapter = X402Adapter()

    @property
    def last_response_headers(self) -> dict[str, str]:
        return dict(self._last_response_headers)

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
        """Return True if global emergency pause or any pause scope is active (§21, §67, AP-008)."""
        return (
            self.emergency_pause_store.is_globally_paused()
            or self._emergency_paused
            or "ALL" in self._paused_scopes
            or len(self._paused_scopes) > 0
        )

    def is_emergency_paused_for(self, tenant_id: str = "") -> bool:
        """Check if emergency pause is active for the platform or a specific tenant (§21, §67, AP-008, B-PAUSE-01)."""
        if self.emergency_pause_store.is_paused_for(tenant_id):
            return True
        if self._emergency_paused or "ALL" in self._paused_scopes:
            return True
        if not tenant_id:
            return False
        clean = tenant_id.strip()
        return clean in self._paused_scopes or f"tenant:{clean}" in self._paused_scopes

    def pause_emergency(self, operator_id: str, reason: str, scope: str = "ALL") -> None:
        """Trigger emergency kill switch (§21, §67, AP-008, B-PAUSE-01)."""
        self.emergency_pause_store.pause(operator_id=operator_id, reason=reason, scope=scope)
        norm_scope = scope.strip() if scope else "ALL"
        upper_scope = norm_scope.upper()
        if (
            upper_scope == "ALL"
            or upper_scope.startswith("ALL")
            or upper_scope in ("GLOBAL", "CLUSTER")
        ):
            self._emergency_paused = True
            self._paused_scopes.add("ALL")
        else:
            self._paused_scopes.add(norm_scope)
            if norm_scope.startswith("tenant:"):
                self._paused_scopes.add(norm_scope[len("tenant:") :])
            else:
                self._paused_scopes.add(f"tenant:{norm_scope}")
        self._pause_metadata = {
            "paused_by": operator_id,
            "reason": reason,
            "scope": norm_scope,
            "paused_at": datetime.now(UTC).isoformat(),
            "active_scopes": sorted(self._paused_scopes),
        }

    def resume_emergency(self, operator_id: str, scope: str = "ALL") -> None:
        """Resume normal autonomous operations (§21, §67, AP-008, B-PAUSE-01)."""
        self.emergency_pause_store.resume(operator_id=operator_id, scope=scope)
        norm_scope = scope.strip() if scope else "ALL"
        upper_scope = norm_scope.upper()
        if (
            upper_scope == "ALL"
            or upper_scope.startswith("ALL")
            or upper_scope in ("GLOBAL", "CLUSTER")
        ):
            self._emergency_paused = False
            self._paused_scopes.clear()
        else:
            self._paused_scopes.discard(norm_scope)
            if norm_scope.startswith("tenant:"):
                self._paused_scopes.discard(norm_scope[len("tenant:") :])
            else:
                self._paused_scopes.discard(f"tenant:{norm_scope}")
            if not self._paused_scopes:
                self._emergency_paused = False
        self._pause_metadata = {
            "resumed_by": operator_id,
            "resumed_at": datetime.now(UTC).isoformat(),
            "scope": norm_scope,
            "active_scopes": sorted(self._paused_scopes),
        }

    def get_emergency_status(self) -> dict[str, Any]:
        """Return current emergency kill switch status (§21, §67, AP-008)."""
        store_status = self.emergency_pause_store.get_status()
        is_paused = self.is_emergency_paused
        scopes = sorted(set(self._paused_scopes) | set(store_status.get("scopes", [])))
        metadata = dict(store_status.get("metadata") or self._pause_metadata)
        return {
            "paused": is_paused,
            "emergency_paused": is_paused,
            "scopes": scopes,
            "metadata": metadata,
        }

    def _error_response(
        self,
        status_code: int,
        code: str,
        message: str,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
        trace_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        effective_trace_id = trace_id or _current_trace_id.get()
        if headers:
            self._last_response_headers.update(headers)
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

    def _is_mutating_route(self, path: str, method: str) -> bool:
        """Determine if an endpoint is a high-risk/mutating financial route (§62)."""
        if method != "POST":
            return False
        return (
            path in ("/api/v1/actions", "/agent/actions")
            or path.startswith("/api/v1/protocols/")
            or path.startswith("/api/v1/tools/")
            or path.startswith("/agent/tools/")
            or (
                path.startswith("/api/v1/approvals/")
                and any(path.endswith(f"/{action}") for action in ("approve", "reject", "grant"))
            )
        )

    def _is_read_route(self, path: str, method: str) -> bool:
        """Determine if an endpoint is a read-only resource route (§62)."""
        return method == "GET" and (
            path == "/agent/catalog"
            or path.startswith("/agent/products/")
            or path == "/api/v1/approvals"
        )

    def _check_rate_limits(
        self,
        path: str,
        method: str,
        tenant_id: str,
        principal_id: str,
        request_id: str,
    ) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        """Evaluate dual-tier rate limiting (agent bucket + tenant bucket) (§52, §62).

        Returns:
            (status_code, error_body, headers) if rate limit exceeded, else None.
        """
        is_mutating = self._is_mutating_route(path, method)
        is_read = self._is_read_route(path, method)

        if not is_mutating and not is_read:
            return None

        if is_mutating:
            agent_cap = self._mutating_agent_capacity
            agent_refill = self._mutating_agent_refill_rate
            tenant_cap = self._mutating_tenant_capacity
            tenant_refill = self._mutating_tenant_refill_rate
            agent_key = f"agent:{tenant_id}:{principal_id}"
            tenant_key = f"tenant:{tenant_id}"
        else:
            agent_cap = self._read_agent_capacity
            agent_refill = self._read_agent_refill_rate
            tenant_cap = self._read_tenant_capacity
            tenant_refill = self._read_tenant_refill_rate
            agent_key = f"agent:read:{tenant_id}:{principal_id}"
            tenant_key = f"tenant:read:{tenant_id}"

        # 1. Check Agent Bucket
        agent_res = self.rate_limiter.check_rate_limit(
            key=agent_key,
            cost=1,
            capacity=agent_cap,
            refill_rate_per_sec=agent_refill,
        )
        if not agent_res.allowed:
            retry_secs = max(1, math.ceil(agent_res.retry_after_seconds))
            details = {
                "retry_after_seconds": agent_res.retry_after_seconds,
                "limit": agent_res.limit,
                "remaining": agent_res.remaining,
                "scope": "agent",
            }
            rl_headers = {
                "retry-after": str(retry_secs),
                "x-ratelimit-limit": str(agent_res.limit),
                "x-ratelimit-remaining": str(agent_res.remaining),
                "x-ratelimit-reset": str(math.ceil(agent_res.reset_time)),
            }
            status, body = self._error_response(
                429,
                "RATE_LIMIT_EXCEEDED",
                f"Rate limit exceeded. Please retry after {retry_secs} seconds.",
                request_id=request_id,
                details=details,
                headers=rl_headers,
            )
            return status, body, rl_headers

        # 2. Check Tenant Bucket
        tenant_res = self.rate_limiter.check_rate_limit(
            key=tenant_key,
            cost=1,
            capacity=tenant_cap,
            refill_rate_per_sec=tenant_refill,
        )
        if not tenant_res.allowed:
            retry_secs = max(1, math.ceil(tenant_res.retry_after_seconds))
            details = {
                "retry_after_seconds": tenant_res.retry_after_seconds,
                "limit": tenant_res.limit,
                "remaining": tenant_res.remaining,
                "scope": "tenant",
            }
            rl_headers = {
                "retry-after": str(retry_secs),
                "x-ratelimit-limit": str(tenant_res.limit),
                "x-ratelimit-remaining": str(tenant_res.remaining),
                "x-ratelimit-reset": str(math.ceil(tenant_res.reset_time)),
            }
            status, body = self._error_response(
                429,
                "RATE_LIMIT_EXCEEDED",
                f"Rate limit exceeded. Please retry after {retry_secs} seconds.",
                request_id=request_id,
                details=details,
                headers=rl_headers,
            )
            return status, body, rl_headers

        # Both passed: store rate limit headers for success responses
        self._last_response_headers.update(
            {
                "x-ratelimit-limit": str(agent_res.limit),
                "x-ratelimit-remaining": str(agent_res.remaining),
                "x-ratelimit-reset": str(math.ceil(agent_res.reset_time)),
            }
        )
        return None

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
        body: Any = None,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        """Synchronous dispatch for hermetic testing and clean request processing."""
        hdrs = {k.lower(): v for k, v in headers.items()}
        request_id = hdrs.get("x-request-id", f"req_{uuid.uuid4().hex[:12]}")
        trace_id = (
            hdrs.get("x-trace-id") or hdrs.get("traceparent") or f"trace_{uuid.uuid4().hex[:12]}"
        )
        tenant_id = hdrs.get("x-tenant-id", "")

        ctx_token = _current_trace_id.set(trace_id)
        self._last_response_headers = {
            "x-trace-id": trace_id,
            "x-request-id": request_id,
        }

        # Request body size boundary enforcement (§62, B-SRV-01)
        if raw_body is not None and len(raw_body) > MAX_REQUEST_BODY_BYTES:
            status_code, resp_data = self._error_response(
                413,
                "PAYLOAD_TOO_LARGE",
                f"Request body exceeds maximum permitted size of {MAX_REQUEST_BODY_BYTES} bytes",
                request_id,
                details={"max_bytes": MAX_REQUEST_BODY_BYTES, "received_bytes": len(raw_body)},
            )
            return ApiResponse(status_code, resp_data, self._last_response_headers)

        content_length_str = hdrs.get("content-length", "")
        if content_length_str:
            try:
                cl = int(content_length_str)
                if cl > MAX_REQUEST_BODY_BYTES:
                    status_code, resp_data = self._error_response(
                        413,
                        "PAYLOAD_TOO_LARGE",
                        f"Request body exceeds maximum permitted size of {MAX_REQUEST_BODY_BYTES} bytes",
                        request_id,
                        details={"max_bytes": MAX_REQUEST_BODY_BYTES, "declared_bytes": cl},
                    )
                    return ApiResponse(status_code, resp_data, self._last_response_headers)
            except ValueError:
                pass

        try:
            status_code, resp_data = self._dispatch(
                method, path, hdrs, body, request_id, tenant_id, trace_id, raw_body=raw_body
            )
            return ApiResponse(status_code, resp_data, self._last_response_headers)
        except RateLimitExceededError as err:
            ceil_secs = max(1, math.ceil(err.retry_after_seconds))
            rl_headers = {
                "retry-after": str(ceil_secs),
            }
            status_code, resp_data = self._error_response(
                429,
                "RATE_LIMIT_EXCEEDED",
                str(err),
                request_id,
                details={
                    "retry_after_seconds": err.retry_after_seconds,
                    "scope": err.scope,
                },
                headers=rl_headers,
            )
            return ApiResponse(status_code, resp_data, self._last_response_headers)
        except UnknownToolError as err:
            status_code, resp_data = self._error_response(400, "UNKNOWN_TOOL", str(err), request_id)
            return ApiResponse(status_code, resp_data, self._last_response_headers)
        except UnauthorizedToolAccessError as err:
            status_code, resp_data = self._error_response(
                403, "UNAUTHORIZED_TOOL_ACCESS", str(err), request_id
            )
            return ApiResponse(status_code, resp_data, self._last_response_headers)
        except Exception:
            status_code, resp_data = self._error_response(
                500,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred",
                request_id,
                trace_id=trace_id,
            )
            return ApiResponse(status_code, resp_data, self._last_response_headers)
        finally:
            _current_trace_id.reset(ctx_token)

    def handle_request_with_headers(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: Any = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        """Dispatch returning explicit 3-tuple (status_code, body, headers)."""
        resp = self.handle_request(method, path, headers, body, raw_body=raw_body)
        return resp.status_code, resp.body, resp.headers

    def _handle_payment_webhook(
        self,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        request_id: str,
        tenant_id: str,
        trace_id: str,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Process incoming provider payment webhook with HMAC validation and replay protection (§61)."""
        if body is None or not isinstance(body, dict):
            return self._error_response(
                400, "INVALID_PAYLOAD", "Missing or invalid JSON payload", request_id
            )

        # 1. Enforce X-Webhook-Signature header
        sig_header = headers.get("x-webhook-signature", "").strip()
        if not sig_header:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Missing or blank X-Webhook-Signature header",
                request_id,
            )

        # 2. Enforce Timestamp Freshness & Skew Window (§61)
        ts_str = headers.get("x-webhook-timestamp", "").strip()
        if not ts_str and "timestamp" in body:
            ts_str = str(body["timestamp"]).strip()

        if not ts_str:
            return self._error_response(
                400,
                "INVALID_WEBHOOK_TIMESTAMP",
                "Missing webhook timestamp in header or payload",
                request_id,
            )

        try:
            ts_num = float(ts_str)
            if ts_num > 1e11:  # epoch milliseconds
                ts_num = ts_num / 1000.0
            event_dt = datetime.fromtimestamp(ts_num, tz=UTC)
        except (ValueError, OverflowError):
            try:
                event_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                return self._error_response(
                    400,
                    "INVALID_WEBHOOK_TIMESTAMP",
                    f"Unparseable webhook timestamp '{ts_str}'",
                    request_id,
                )

        age_seconds = (datetime.now(UTC) - event_dt).total_seconds()
        # Enforce timestamp window: past age > 300s rejected, future skew > 30s rejected (§61)
        if age_seconds > 300.0 or age_seconds < -30.0:
            return self._error_response(
                401,
                "EXPIRED_WEBHOOK_TIMESTAMP",
                f"Webhook timestamp expired or future skew exceeds tolerance ({int(age_seconds)}s outside -30s..300s window)",
                request_id,
                details={
                    "age_seconds": age_seconds,
                    "max_age_seconds": 300,
                    "max_future_skew_seconds": 30,
                },
            )

        # 3. Compute and Verify HMAC-SHA256 Signature (Supports Raw PSP Bytes & Canonical Serialization)
        header_ts = headers.get("x-webhook-timestamp", "").strip()
        raw_json_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
        canonical = f"{header_ts}.{raw_json_str}" if header_ts else raw_json_str

        candidate_byte_payloads: list[bytes] = []
        if raw_body:
            candidate_byte_payloads.append(raw_body)
            if header_ts:
                candidate_byte_payloads.append(f"{header_ts}.".encode() + raw_body)

        candidate_byte_payloads.append(canonical.encode("utf-8"))
        candidate_byte_payloads.append(raw_json_str.encode("utf-8"))

        received_sigs: list[str] = []
        if "," in sig_header:
            for part in sig_header.split(","):
                part_s = part.strip()
                if part_s.startswith("v1="):
                    received_sigs.append(part_s[3:])
                elif part_s.startswith("sha256="):
                    received_sigs.append(part_s[7:])
        else:
            rec = sig_header
            if rec.startswith("sha256="):
                rec = rec[7:]
            elif rec.startswith("v1="):
                rec = rec[3:]
            received_sigs.append(rec)

        secret_bytes = self.webhook_secret.encode("utf-8")
        signature_valid = False
        for cand_bytes in candidate_byte_payloads:
            expected_sig = hmac.new(secret_bytes, cand_bytes, hashlib.sha256).hexdigest()
            for r_sig in received_sigs:
                if hmac.compare_digest(expected_sig, r_sig):
                    signature_valid = True
                    break
            if signature_valid:
                break

        if not signature_valid:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Webhook HMAC signature verification failed",
                request_id,
            )

        # 4. Enforce Replay Protection via Global Event Registry & IdempotencyStore (§42, §61)
        effective_tenant = (
            headers.get("x-tenant-id", "").strip()
            or str(body.get("tenant_id") or "").strip()
            or tenant_id
        )

        event_id = str(
            body.get("event_id") or headers.get("x-webhook-id") or body.get("id") or ""
        ).strip()

        if not event_id:
            return self._error_response(
                400,
                "INVALID_PAYLOAD",
                "Missing event_id in payload or X-Webhook-Id header",
                request_id,
            )

        event_type = str(
            body.get("event_type") or body.get("event") or body.get("type") or ""
        ).strip()

        if not event_type:
            return self._error_response(
                400,
                "INVALID_PAYLOAD",
                "Missing event_type in webhook payload",
                request_id,
            )

        provider = str(body.get("provider") or "payment").strip().lower()
        provider_account_id = str(
            body.get("account_id") or headers.get("x-account-id") or ""
        ).strip()
        replay_key = f"webhook:{event_id}"
        cache_key = f"{provider}:{provider_account_id}:{event_id}"
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        with self._webhook_lock:
            if cache_key in self._processed_webhook_events:
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Webhook event '{event_id}' has already been processed",
                    request_id,
                    details={"event_id": event_id, "provider": provider, "replay_key": replay_key},
                )

            if effective_tenant:
                existing_record = self.idempotency_store.get(effective_tenant, replay_key)
                if existing_record is not None:
                    self._processed_webhook_events.add(cache_key)
                    return self._error_response(
                        409,
                        "WEBHOOK_REPLAY_DETECTED",
                        f"Webhook event '{event_id}' has already been processed",
                        request_id,
                        details={
                            "event_id": event_id,
                            "provider": provider,
                            "replay_key": replay_key,
                        },
                    )

            claimed_event, is_new = self.webhook_event_repo.claim_event(
                provider=provider,
                provider_account_id=provider_account_id,
                event_id=event_id,
                payload_hash=payload_hash,
                event_type=event_type,
                trace_id=trace_id,
            )
            if not is_new:
                self._processed_webhook_events.add(cache_key)
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Webhook event '{event_id}' has already been processed",
                    request_id,
                    details={
                        "event_id": event_id,
                        "provider": provider,
                        "replay_key": replay_key,
                        "status": claimed_event.status.value,
                    },
                )

            # 5. Dispatch to PaymentReconciler under lock
            try:
                reconcile_result = self.reconciler.reconcile_webhook_event(
                    tenant_id=effective_tenant,
                    event_type=event_type,
                    payload=body,
                    trace_id=trace_id,
                )
            except DomainError as err:
                self.webhook_event_repo.save(claimed_event.mark_failed(err.code))
                return self._error_response(
                    400,
                    err.code,
                    str(err),
                    request_id,
                    details=getattr(err, "details", {}),
                )
            except Exception as err:
                self.webhook_event_repo.save(claimed_event.mark_failed("RECONCILIATION_FAILED"))
                return self._error_response(
                    500,
                    "RECONCILIATION_FAILED",
                    f"Webhook reconciliation failed: {err}",
                    request_id,
                )

            if reconcile_result.get("status") == "QUARANTINED":
                self.webhook_event_repo.save(
                    claimed_event.mark_quarantined("UNRESOLVED_WEBHOOK_TRANSACTION")
                )
                return self._error_response(
                    422,
                    "UNRESOLVED_WEBHOOK_TRANSACTION",
                    "Unmapped or unresolvable webhook transaction",
                    request_id,
                    details=reconcile_result,
                )

            final_tenant = str(reconcile_result.get("tenant_id") or effective_tenant).strip()
            if not final_tenant:
                self.webhook_event_repo.save(claimed_event.mark_failed("MISSING_TENANT"))
                return self._error_response(
                    400,
                    "INVALID_PAYLOAD",
                    "Missing tenant identifier in headers or webhook payload",
                    request_id,
                )

            # Mark processed in durable repository
            entity_ref = str(
                reconcile_result.get("attempt_id") or reconcile_result.get("order_id") or ""
            )
            self.webhook_event_repo.save(claimed_event.mark_processed(entity_reference=entity_ref))

            # Save idempotency record to prevent future replay
            self.idempotency_store.save(
                final_tenant,
                IdempotencyRecord(
                    key=replay_key,
                    tenant_id=final_tenant,
                    operation_type="webhook",
                    request_hash=payload_hash,
                    result_data=reconcile_result,
                ),
            )
            self._processed_webhook_events.add(cache_key)

        return 200, reconcile_result

    def _handle_razorpay_webhook(
        self,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        request_id: str,
        tenant_id: str,
        trace_id: str,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Process incoming Razorpay webhook with raw-body HMAC-SHA256 verification (§61)."""
        raw_bytes: bytes = b""
        if raw_body is not None:
            raw_bytes = raw_body
        elif body is not None:
            raw_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        else:
            return self._error_response(
                400, "INVALID_PAYLOAD", "Missing or invalid JSON payload", request_id
            )

        # 1. Enforce X-Razorpay-Signature header
        sig_header = headers.get("x-razorpay-signature", "").strip()
        if not sig_header:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Missing or blank X-Razorpay-Signature header",
                request_id,
            )

        # 2. Cryptographic constant-time HMAC-SHA256 verification
        secret_candidates = (
            [s.strip() for s in self.razorpay_webhook_secret.split(",") if s.strip()]
            if "," in self.razorpay_webhook_secret
            else [self.razorpay_webhook_secret]
        )

        signature_valid = False
        for sec in secret_candidates:
            expected_sig = hmac.new(sec.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected_sig, sig_header):
                signature_valid = True
                break

        if not signature_valid:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Razorpay webhook HMAC signature verification failed",
                request_id,
            )

        # 3. Parse JSON body
        parsed_body = body
        if parsed_body is None:
            try:
                parsed_body = json.loads(raw_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._error_response(
                    400, "INVALID_PAYLOAD", "Malformed JSON payload", request_id
                )

        if not isinstance(parsed_body, dict):
            return self._error_response(
                400, "INVALID_PAYLOAD", "Webhook body must be a JSON object", request_id
            )

        # 4. Extract Event Metadata
        event_id = str(
            headers.get("x-razorpay-event-id")
            or headers.get("x-webhook-id")
            or parsed_body.get("event_id")
            or parsed_body.get("id")
            or ""
        ).strip()

        event_name = str(
            parsed_body.get("event")
            or parsed_body.get("event_type")
            or parsed_body.get("type")
            or ""
        ).strip()
        if not event_name:
            return self._error_response(
                400, "INVALID_PAYLOAD", "Missing event type in Razorpay webhook payload", request_id
            )

        type_mapping = {
            "payment.captured": "payment.captured",
            "payment.failed": "payment.failed",
            "refund.processed": "payment.refunded",
        }
        canonical_event_type = type_mapping.get(event_name, event_name)

        payload_entity = parsed_body.get("payload", {})
        payment_entity = (
            payload_entity.get("payment", {}).get("entity", {})
            if isinstance(payload_entity, dict)
            else {}
        )
        notes = payment_entity.get("notes", {}) if isinstance(payment_entity, dict) else {}

        prov_payment_id = str(
            payment_entity.get("id")
            or parsed_body.get("provider_payment_id")
            or parsed_body.get("id")
            or ""
        ).strip()
        candidate_attempt = str(
            notes.get("attempt_id")
            or notes.get("payment_attempt_id")
            or parsed_body.get("attempt_id")
            or parsed_body.get("payment_id")
            or ""
        ).strip()

        if not event_id:
            event_id = (
                f"rzp_{prov_payment_id}_{canonical_event_type}"
                if prov_payment_id
                else f"evt_rzp_{uuid.uuid4().hex[:12]}"
            )

        # 5. Replay Protection
        prov_account_id = str(parsed_body.get("account_id") or "").strip()
        payload_hash = hashlib.sha256(raw_bytes).hexdigest()
        replay_key = f"webhook:razorpay:{event_id}"
        cache_key = f"razorpay:{prov_account_id}:{event_id}"

        with self._webhook_lock:
            if cache_key in self._processed_webhook_events:
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Razorpay webhook event '{event_id}' has already been processed",
                    request_id,
                    details={
                        "event_id": event_id,
                        "provider": "razorpay",
                        "replay_key": replay_key,
                    },
                )

            effective_tenant = (
                headers.get("x-tenant-id", "").strip()
                or str(notes.get("tenant_id") or parsed_body.get("tenant_id") or "").strip()
                or tenant_id
            )
            if effective_tenant:
                existing_record = self.idempotency_store.get(effective_tenant, replay_key)
                if existing_record is not None:
                    self._processed_webhook_events.add(cache_key)
                    return self._error_response(
                        409,
                        "WEBHOOK_REPLAY_DETECTED",
                        f"Razorpay webhook event '{event_id}' has already been processed",
                        request_id,
                        details={
                            "event_id": event_id,
                            "provider": "razorpay",
                            "replay_key": replay_key,
                        },
                    )

            claimed_event, is_new = self.webhook_event_repo.claim_event(
                provider="razorpay",
                provider_account_id=prov_account_id,
                event_id=event_id,
                payload_hash=payload_hash,
                event_type=canonical_event_type,
                trace_id=trace_id,
            )
            if not is_new:
                self._processed_webhook_events.add(cache_key)
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Razorpay webhook event '{event_id}' has already been processed",
                    request_id,
                    details={
                        "event_id": event_id,
                        "provider": "razorpay",
                        "replay_key": replay_key,
                        "status": claimed_event.status.value,
                    },
                )

            # 6. Normalize payload for Reconciler
            reconcile_payload = {
                **parsed_body,
                "provider": "razorpay",
                "provider_payment_id": prov_payment_id,
                "attempt_id": candidate_attempt,
                "tenant_id": effective_tenant,
                "event_type": canonical_event_type,
                "data": {
                    "provider_payment_id": prov_payment_id,
                    "amount": payment_entity.get("amount") or parsed_body.get("amount"),
                    "currency": (
                        payment_entity.get("currency") or parsed_body.get("currency") or "INR"
                    ).upper(),
                    "status": payment_entity.get("status") or parsed_body.get("status"),
                    "error_description": payment_entity.get("error_description"),
                    "reason": payment_entity.get("error_description"),
                },
            }

            try:
                reconcile_result = self.reconciler.reconcile_webhook_event(
                    tenant_id=effective_tenant,
                    event_type=canonical_event_type,
                    payload=reconcile_payload,
                    trace_id=trace_id,
                )
            except DomainError as err:
                self.webhook_event_repo.save(claimed_event.mark_failed(err.code))
                return self._error_response(
                    400,
                    err.code,
                    str(err),
                    request_id,
                    details=getattr(err, "details", {}),
                )
            except Exception as err:
                self.webhook_event_repo.save(claimed_event.mark_failed("RECONCILIATION_FAILED"))
                return self._error_response(
                    500,
                    "RECONCILIATION_FAILED",
                    f"Razorpay webhook reconciliation failed: {err}",
                    request_id,
                )

            if reconcile_result.get("status") == "QUARANTINED":
                self.webhook_event_repo.save(
                    claimed_event.mark_quarantined("UNRESOLVED_WEBHOOK_TRANSACTION")
                )
                return self._error_response(
                    422,
                    "UNRESOLVED_WEBHOOK_TRANSACTION",
                    "Unmapped or unresolvable webhook transaction",
                    request_id,
                    details=reconcile_result,
                )

            entity_ref = str(reconcile_result.get("attempt_id") or "")
            self.webhook_event_repo.save(claimed_event.mark_processed(entity_reference=entity_ref))

            final_tenant = str(reconcile_result.get("tenant_id") or effective_tenant).strip()
            if final_tenant:
                self.idempotency_store.save(
                    final_tenant,
                    IdempotencyRecord(
                        key=replay_key,
                        tenant_id=final_tenant,
                        operation_type="webhook",
                        request_hash=payload_hash,
                        result_data=reconcile_result,
                    ),
                )
            self._processed_webhook_events.add(cache_key)

        return 200, reconcile_result

    def _handle_stripe_webhook(
        self,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        request_id: str,
        tenant_id: str,
        trace_id: str,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Process incoming Stripe webhook with timestamped Stripe-Signature HMAC-SHA256 verification (§61)."""
        raw_bytes: bytes = b""
        if raw_body is not None:
            raw_bytes = raw_body
        elif body is not None:
            raw_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        else:
            return self._error_response(
                400, "INVALID_PAYLOAD", "Missing or invalid JSON payload", request_id
            )

        # 1. Enforce Stripe-Signature header
        sig_header = headers.get("stripe-signature", "").strip()
        if not sig_header:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Missing or blank Stripe-Signature header",
                request_id,
            )

        # 2. Parse Stripe-Signature components (t=..., v1=...)
        parts = sig_header.split(",")
        ts_str: str | None = None
        signatures: list[str] = []
        for part in parts:
            item = part.strip()
            if item.startswith("t="):
                ts_str = item[2:]
            elif item.startswith("v1="):
                signatures.append(item[3:])

        if not ts_str or not signatures:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Invalid Stripe-Signature header format; must contain t= and v1=",
                request_id,
            )

        # 3. Enforce Timestamp Window (300s max past age, 30s max future skew)
        try:
            ts_val = int(ts_str)
        except ValueError:
            return self._error_response(
                400,
                "INVALID_WEBHOOK_TIMESTAMP",
                f"Unparseable Stripe timestamp '{ts_str}'",
                request_id,
            )

        age_seconds = time.time() - ts_val
        if age_seconds > 300.0 or age_seconds < -30.0:
            return self._error_response(
                401,
                "EXPIRED_WEBHOOK_TIMESTAMP",
                f"Stripe webhook timestamp expired or future skew exceeds tolerance ({int(age_seconds)}s outside -30s..300s window)",
                request_id,
                details={
                    "age_seconds": age_seconds,
                    "max_age_seconds": 300,
                    "max_future_skew_seconds": 30,
                },
            )

        # 4. Cryptographic constant-time HMAC-SHA256 verification
        signed_payload = f"{ts_val}.".encode() + raw_bytes
        secret_candidates = (
            [s.strip() for s in self.stripe_webhook_secret.split(",") if s.strip()]
            if "," in self.stripe_webhook_secret
            else [self.stripe_webhook_secret]
        )

        signature_valid = False
        for sec in secret_candidates:
            expected_sig = hmac.new(sec.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
            for sig in signatures:
                if hmac.compare_digest(expected_sig, sig):
                    signature_valid = True
                    break
            if signature_valid:
                break

        if not signature_valid:
            return self._error_response(
                401,
                "INVALID_WEBHOOK_SIGNATURE",
                "Stripe webhook HMAC signature verification failed",
                request_id,
            )

        # 5. Parse JSON body
        parsed_body = body
        if parsed_body is None:
            try:
                parsed_body = json.loads(raw_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._error_response(
                    400, "INVALID_PAYLOAD", "Malformed JSON payload", request_id
                )

        if not isinstance(parsed_body, dict):
            return self._error_response(
                400, "INVALID_PAYLOAD", "Webhook body must be a JSON object", request_id
            )

        # 6. Extract Event Metadata
        event_id = str(
            headers.get("x-stripe-event-id")
            or headers.get("x-webhook-id")
            or parsed_body.get("id")
            or parsed_body.get("event_id")
            or ""
        ).strip()

        event_name = str(
            parsed_body.get("type")
            or parsed_body.get("event_type")
            or parsed_body.get("event")
            or ""
        ).strip()
        if not event_name:
            return self._error_response(
                400, "INVALID_PAYLOAD", "Missing event type in Stripe webhook payload", request_id
            )

        type_mapping = {
            "payment_intent.succeeded": "payment.captured",
            "payment_intent.payment_failed": "payment.failed",
            "charge.refunded": "payment.refunded",
        }
        canonical_event_type = type_mapping.get(event_name, event_name)

        raw_data = parsed_body.get("data")
        data_dict: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        raw_obj = data_dict.get("object")
        obj: dict[str, Any] = raw_obj if isinstance(raw_obj, dict) else {}
        raw_meta = obj.get("metadata")
        metadata: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}

        prov_payment_id = str(obj.get("id") or parsed_body.get("provider_payment_id") or "").strip()
        candidate_attempt = str(
            metadata.get("attempt_id")
            or obj.get("metadata_attempt_id")
            or parsed_body.get("attempt_id")
            or parsed_body.get("payment_id")
            or ""
        ).strip()

        if not event_id:
            event_id = f"evt_stripe_{uuid.uuid4().hex[:12]}"

        # 7. Replay Protection
        prov_account_id = str(parsed_body.get("account") or "").strip()
        payload_hash = hashlib.sha256(signed_payload).hexdigest()
        replay_key = f"webhook:stripe:{event_id}"
        cache_key = f"stripe:{prov_account_id}:{event_id}"

        with self._webhook_lock:
            if cache_key in self._processed_webhook_events:
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Stripe webhook event '{event_id}' has already been processed",
                    request_id,
                    details={"event_id": event_id, "provider": "stripe", "replay_key": replay_key},
                )

            effective_tenant = (
                headers.get("x-tenant-id", "").strip()
                or str(metadata.get("tenant_id") or parsed_body.get("tenant_id") or "").strip()
                or tenant_id
            )
            if effective_tenant:
                existing_record = self.idempotency_store.get(effective_tenant, replay_key)
                if existing_record is not None:
                    self._processed_webhook_events.add(cache_key)
                    return self._error_response(
                        409,
                        "WEBHOOK_REPLAY_DETECTED",
                        f"Stripe webhook event '{event_id}' has already been processed",
                        request_id,
                        details={
                            "event_id": event_id,
                            "provider": "stripe",
                            "replay_key": replay_key,
                        },
                    )

            claimed_event, is_new = self.webhook_event_repo.claim_event(
                provider="stripe",
                provider_account_id=prov_account_id,
                event_id=event_id,
                payload_hash=payload_hash,
                event_type=canonical_event_type,
                trace_id=trace_id,
            )
            if not is_new:
                self._processed_webhook_events.add(cache_key)
                return self._error_response(
                    409,
                    "WEBHOOK_REPLAY_DETECTED",
                    f"Stripe webhook event '{event_id}' has already been processed",
                    request_id,
                    details={
                        "event_id": event_id,
                        "provider": "stripe",
                        "replay_key": replay_key,
                        "status": claimed_event.status.value,
                    },
                )

            # 8. Normalize payload for Reconciler
            raw_last_err = obj.get("last_payment_error")
            last_err: dict[str, Any] = raw_last_err if isinstance(raw_last_err, dict) else {}
            failure_msg = last_err.get("message")

            reconcile_payload = {
                **parsed_body,
                "provider": "stripe",
                "provider_payment_id": prov_payment_id,
                "attempt_id": candidate_attempt,
                "tenant_id": effective_tenant,
                "event_type": canonical_event_type,
                "data": {
                    "provider_payment_id": prov_payment_id,
                    "amount": obj.get("amount") or parsed_body.get("amount"),
                    "currency": (
                        obj.get("currency") or parsed_body.get("currency") or "INR"
                    ).upper(),
                    "status": obj.get("status") or parsed_body.get("status"),
                    "failure_reason": failure_msg,
                    "reason": failure_msg,
                },
            }

            try:
                reconcile_result = self.reconciler.reconcile_webhook_event(
                    tenant_id=effective_tenant,
                    event_type=canonical_event_type,
                    payload=reconcile_payload,
                    trace_id=trace_id,
                )
            except DomainError as err:
                self.webhook_event_repo.save(claimed_event.mark_failed(err.code))
                return self._error_response(
                    400,
                    err.code,
                    str(err),
                    request_id,
                    details=getattr(err, "details", {}),
                )
            except Exception as err:
                self.webhook_event_repo.save(claimed_event.mark_failed("RECONCILIATION_FAILED"))
                return self._error_response(
                    500,
                    "RECONCILIATION_FAILED",
                    f"Stripe webhook reconciliation failed: {err}",
                    request_id,
                )

            if reconcile_result.get("status") == "QUARANTINED":
                self.webhook_event_repo.save(
                    claimed_event.mark_quarantined("UNRESOLVED_WEBHOOK_TRANSACTION")
                )
                return self._error_response(
                    422,
                    "UNRESOLVED_WEBHOOK_TRANSACTION",
                    "Unmapped or unresolvable webhook transaction",
                    request_id,
                    details=reconcile_result,
                )

            entity_ref = str(reconcile_result.get("attempt_id") or "")
            self.webhook_event_repo.save(claimed_event.mark_processed(entity_reference=entity_ref))

            final_tenant = str(reconcile_result.get("tenant_id") or effective_tenant).strip()
            if final_tenant:
                self.idempotency_store.save(
                    final_tenant,
                    IdempotencyRecord(
                        key=replay_key,
                        tenant_id=final_tenant,
                        operation_type="webhook",
                        request_hash=payload_hash,
                        result_data=reconcile_result,
                    ),
                )
            self._processed_webhook_events.add(cache_key)

        return 200, reconcile_result

    def _dispatch(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: Any,
        request_id: str,
        tenant_id: str,
        trace_id: str = "",
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:

        # Guard against non-object JSON bodies on mutating routes (§13, B-API-01)
        if method in ("POST", "PUT", "PATCH") and body is not None and not isinstance(body, dict):
            if path in (
                "/api/v1/webhooks/payments",
                "/api/v1/webhooks/razorpay",
                "/api/v1/webhooks/stripe",
            ):
                return self._error_response(
                    400, "INVALID_PAYLOAD", "Missing or invalid JSON payload", request_id
                )
            return self._error_response(
                422,
                "INVALID_REQUEST_BODY",
                "Request body must be a JSON object",
                request_id,
                details={"received_type": type(body).__name__},
            )

        # 1. Health check
        if path == "/api/v1/health" and method == "GET":
            resp: dict[str, Any] = {
                "status": "healthy",
                "emergency_paused": self._emergency_paused,
                "version": "1.0.0-mvp",
            }
            if self.config is not None:
                resp["environment"] = self.config.environment.value
                resp["payment_provider"] = self.config.payment_provider.value
                resp["decision_engine"] = self.config.decision_engine.value
            return 200, resp

        # 1a. System Configuration Diagnostics (§13, AP-005)
        if path == "/api/v1/system/config" and method == "GET":
            if self.config is not None:
                return 200, self.config.safe_summary()
            return 200, {
                "environment": "local",
                "payment_provider": "mock",
                "decision_engine": "mock",
                "persistence": "in_memory",
                "demo_data_seeded": True,
                "require_observability_auth": False,
                "capabilities": {
                    "payments_live": False,
                    "razorpay_configured": False,
                    "stripe_configured": False,
                    "model_gateway_enabled": False,
                },
            }

        # 1b. Observability Metrics (§47, B-OBS-01)
        if path == "/api/v1/metrics" and method == "GET":
            if self.require_observability_auth:
                cred, auth_err = self._authenticate_request(headers, request_id)
                if auth_err is not None or cred is None:
                    return (
                        auth_err
                        if auth_err is not None
                        else self._error_response(
                            401,
                            "UNAUTHENTICATED",
                            "Authentication required for observability metrics",
                            request_id,
                        )
                    )
                if not cred.is_operator_or_admin():
                    return self._error_response(
                        403,
                        "UNAUTHORIZED_OBSERVABILITY_ACCESS",
                        "Metrics inspection requires AGENT_OPERATOR or ADMIN role",
                        request_id,
                        details={
                            "principal_id": cred.principal_id,
                            "role": cred.role.value if cred.role else None,
                            "required_roles": ["ADMIN", "AGENT_OPERATOR"],
                        },
                    )
            elif headers.get("authorization") or headers.get("x-api-key"):
                _, auth_err = self._authenticate_request(headers, request_id)
                if auth_err is not None:
                    return auth_err

            from agentpay.infrastructure.observability import get_meter

            meter = get_meter()
            return 200, {
                "metrics": meter.collect(),
                "prometheus": meter.to_prometheus(),
            }

        # 1c. Supply Chain Security SBOM & Provenance (§57, §130, B-OBS-01)
        if path == "/api/v1/security/sbom" and method == "GET":
            if self.require_observability_auth:
                cred, auth_err = self._authenticate_request(headers, request_id)
                if auth_err is not None or cred is None:
                    return (
                        auth_err
                        if auth_err is not None
                        else self._error_response(
                            401,
                            "UNAUTHENTICATED",
                            "Authentication required for SBOM and provenance access",
                            request_id,
                        )
                    )
                if not cred.is_operator_or_admin():
                    return self._error_response(
                        403,
                        "UNAUTHORIZED_OBSERVABILITY_ACCESS",
                        "SBOM metadata inspection requires AGENT_OPERATOR or ADMIN role",
                        request_id,
                        details={
                            "principal_id": cred.principal_id,
                            "role": cred.role.value if cred.role else None,
                            "required_roles": ["ADMIN", "AGENT_OPERATOR"],
                        },
                    )
            elif headers.get("authorization") or headers.get("x-api-key"):
                _, auth_err = self._authenticate_request(headers, request_id)
                if auth_err is not None:
                    return auth_err

            from agentpay.security.sbom import get_sbom_metadata

            return 200, get_sbom_metadata()

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
                    "/api/v1/webhooks/payments": {
                        "post": {"summary": "Payment Provider Webhook Ingestion"}
                    },
                    "/api/v1/webhooks/razorpay": {
                        "post": {"summary": "Razorpay Webhook Ingestion"}
                    },
                    "/api/v1/webhooks/stripe": {"post": {"summary": "Stripe Webhook Ingestion"}},
                    "/api/v1/security/sbom": {
                        "get": {"summary": "CycloneDX 1.5 SBOM & SLSA Provenance"}
                    },
                },
            }

        # 2.5 Webhook Ingestion (§61) - Dedicated unauthenticated/signature-verified routes!
        if path == "/api/v1/webhooks/payments" and method == "POST":
            return self._handle_payment_webhook(
                headers, body, request_id, tenant_id, trace_id, raw_body=raw_body
            )

        if path == "/api/v1/webhooks/razorpay" and method == "POST":
            return self._handle_razorpay_webhook(
                headers, body, request_id, tenant_id, trace_id, raw_body=raw_body
            )

        if path == "/api/v1/webhooks/stripe" and method == "POST":
            return self._handle_stripe_webhook(
                headers, body, request_id, tenant_id, trace_id, raw_body=raw_body
            )

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

        # 3. Emergency Status & Controls (§21, §67, B-PAUSE-01)
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
                    "paused": self.is_emergency_paused,
                    "emergency_paused": self.is_emergency_paused,
                    "scopes": sorted(self._paused_scopes),
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
                    "scopes": sorted(self._paused_scopes),
                    "metadata": self._pause_metadata,
                }

            if path == "/api/v1/emergency/resume" and method == "POST":
                req_data = body or {}
                operator_id = str(req_data.get("operator_id") or cred.principal_id)
                scope = str(req_data.get("scope", "ALL"))
                self.resume_emergency(operator_id=operator_id, scope=scope)
                is_still_paused = self.is_emergency_paused
                return 200, {
                    "status": "PAUSED" if is_still_paused else "ACTIVE",
                    "paused": is_still_paused,
                    "emergency_paused": is_still_paused,
                    "scopes": sorted(self._paused_scopes),
                    "metadata": self._pause_metadata,
                }

            return self._error_response(
                404, "ENDPOINT_NOT_FOUND", f"Cannot {method} {path}", request_id
            )

        # --- TENANT BINDING ENFORCEMENT FOR TENANT-SCOPED ROUTES (§49, §50) ---
        if not tenant_id and path.startswith("/api/v1/protocols/"):
            tenant_id = self._extract_protocol_tenant(path, body)

        if not tenant_id:
            if self.config and self.config.is_production_like:
                tenant_id = cred.tenant_id
            else:
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

        # --- RATE LIMITING ENFORCEMENT (§52, §62) ---
        effective_principal = cred.principal_id
        if isinstance(body, dict) and body.get("agent_id"):
            effective_principal = str(body["agent_id"])

        rate_limit_err = self._check_rate_limits(
            path=path,
            method=method,
            tenant_id=tenant_id,
            principal_id=effective_principal,
            request_id=request_id,
        )
        if rate_limit_err is not None:
            status_code, err_body, _ = rate_limit_err
            return status_code, err_body

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
                    trace_id=trace_id,
                )
                res_dict["trace_id"] = trace_id
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
            if self.is_emergency_paused_for(tenant_id):
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
                    approval_id=req.get("approval_id"),
                )
            except (KeyError, ValueError, TypeError) as err:
                return self._error_response(
                    422, "INVALID_REQUEST_BODY", f"Missing or invalid field: {err}", request_id
                )

            # --- AGENT IMPERSONATION PREVENTION (§14, §15, AP-009) ---
            if cred.role is None and sub.agent_id != cred.principal_id:
                return self._error_response(
                    403,
                    "AGENT_IMPERSONATION_DENIED",
                    f"Authenticated agent '{cred.principal_id}' cannot act as agent '{sub.agent_id}'",
                    request_id,
                    details={
                        "authenticated_agent": cred.principal_id,
                        "asserted_agent": sub.agent_id,
                    },
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
                if sub.approval_id:
                    res: PurchaseExecutionResult = self.orchestrator.execute_approved_purchase(
                        approval_id=sub.approval_id,
                        action=action,
                        tenant_id=tenant_id,
                        variant_id=sub.variant_id,
                        quantity=sub.quantity,
                        idempotency_key=sub.idempotency_key,
                        trace_id=trace_id,
                    )
                else:
                    res = self.orchestrator.execute_purchase(
                        action=action,
                        tenant_id=tenant_id,
                        variant_id=sub.variant_id,
                        quantity=sub.quantity,
                        idempotency_key=sub.idempotency_key,
                        trace_id=trace_id,
                    )
            except ApprovalAlreadyConsumedError as err:
                return self._error_response(409, "APPROVAL_ALREADY_CONSUMED", str(err), request_id)
            except ApprovalBindingMismatchError as err:
                return self._error_response(422, "APPROVAL_BINDING_MISMATCH", str(err), request_id)
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
                "trace_id": trace_id,
            }

        # 6. Approvals (GET /api/v1/approvals or GET /api/v1/approvals/{id} or POST /api/v1/approvals/{id}/*)
        if path == "/api/v1/approvals" and method == "GET":
            pending_list: list[dict[str, Any]] = []
            if self.orchestrator._approval_repo is not None:
                repo = self.orchestrator._approval_repo
                pending_approvals = repo.list_pending(tenant_id)
                for a in pending_approvals:
                    pending_list.append(
                        {
                            "approval_id": a.approval_id,
                            "tenant_id": a.tenant_id,
                            "principal_id": a.binding.principal_id,
                            "action_id": a.binding.action_id,
                            "status": a.status.value,
                            "amount_minor": a.binding.amount.minor_units,
                            "currency": a.binding.amount.currency,
                            "merchant_id": a.binding.merchant_id,
                            "resource_id": a.binding.resource_id,
                            "action_version": a.binding.action_version,
                            "requested_at": a.requested_at.isoformat(),
                        }
                    )
            return 200, {"approvals": pending_list}

        if path.startswith("/api/v1/approvals/") and method == "GET":
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                approval_id = parts[3]
                if self.orchestrator._approval_repo is None:
                    return self._error_response(
                        404, "NOT_FOUND", "Approval repository not configured", request_id
                    )
                appr_obj = self.orchestrator._approval_repo.get(tenant_id, approval_id)
                if appr_obj is None:
                    return self._error_response(
                        404, "NOT_FOUND", f"Approval '{approval_id}' not found", request_id
                    )
                return 200, {
                    "approval_id": appr_obj.approval_id,
                    "tenant_id": appr_obj.tenant_id,
                    "principal_id": appr_obj.binding.principal_id,
                    "action_id": appr_obj.binding.action_id,
                    "status": appr_obj.status.value,
                    "amount_minor": appr_obj.binding.amount.minor_units,
                    "currency": appr_obj.binding.amount.currency,
                    "merchant_id": appr_obj.binding.merchant_id,
                    "resource_id": appr_obj.binding.resource_id,
                    "action_version": appr_obj.binding.action_version,
                    "requested_at": appr_obj.requested_at.isoformat(),
                    "approver_id": appr_obj.approver_id,
                    "decided_at": appr_obj.decided_at.isoformat() if appr_obj.decided_at else None,
                    "reason": appr_obj.reason,
                }

        if path.startswith("/api/v1/approvals/") and method == "POST":
            parts = path.strip("/").split("/")
            if len(parts) == 5:
                approval_id = parts[3]
                sub_action = parts[4]
                req = body or {}

                if sub_action == "execute":
                    if self.is_emergency_paused_for(tenant_id):
                        return self._error_response(
                            503,
                            "EMERGENCY_PAUSE_ACTIVE",
                            "Autonomous payment processing is temporarily suspended by emergency control",
                            request_id,
                        )
                    appr = (
                        self.orchestrator._approval_repo.get(tenant_id, approval_id)
                        if self.orchestrator._approval_repo
                        else None
                    )
                    if appr is None:
                        return self._error_response(
                            404, "NOT_FOUND", f"Approval '{approval_id}' not found", request_id
                        )

                    action_id = str(req.get("action_id") or appr.binding.action_id)
                    agent_id = str(req.get("agent_id") or "agent_01")
                    resource_id = str(req.get("resource_id") or appr.binding.resource_id)
                    variant_id = str(req.get("variant_id") or resource_id)
                    quantity = int(req.get("quantity", 1))
                    amount_minor = int(req.get("amount_minor", appr.binding.amount.minor_units))
                    currency = str(req.get("currency", appr.binding.amount.currency))

                    body_principal = req.get("principal_id")
                    if body_principal:
                        principal_id = str(body_principal)
                    elif cred.role == UserRole.ADMIN:
                        principal_id = appr.binding.principal_id
                    else:
                        principal_id = cred.principal_id

                    action = ActionRequest(
                        action_id=action_id,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        agent_id=agent_id,
                        domain=str(req.get("domain", "commerce")),
                        action=str(req.get("action", "purchase")),
                        resource=ResourceRef(
                            type=str(req.get("resource_type", "product")), id=resource_id
                        ),
                        requested_at=datetime.now(UTC),
                        intent=Intent(purpose=str(req.get("intent_purpose", "procurement"))),
                        amount=Money(amount_minor, currency),
                        authorization_mode=AuthorizationMode.AUTONOMOUS,
                        version=appr.binding.action_version,
                    )
                    try:
                        res = self.orchestrator.execute_approved_purchase(
                            approval_id=approval_id,
                            action=action,
                            tenant_id=tenant_id,
                            variant_id=variant_id,
                            quantity=quantity,
                            idempotency_key=req.get("idempotency_key"),
                            trace_id=trace_id,
                        )
                    except ApprovalAlreadyConsumedError as err:
                        return self._error_response(
                            409, "APPROVAL_ALREADY_CONSUMED", str(err), request_id
                        )
                    except ApprovalBindingMismatchError as err:
                        return self._error_response(
                            422, "APPROVAL_BINDING_MISMATCH", str(err), request_id
                        )
                    except CrossTenantAccessError as err:
                        return self._error_response(
                            403, "CROSS_TENANT_ACCESS_DENIED", str(err), request_id
                        )
                    except PrivilegeEscalationAttemptError as err:
                        return self._error_response(
                            403, "PRIVILEGE_ESCALATION_DENIED", str(err), request_id
                        )
                    except InsufficientInventoryError as err:
                        return self._error_response(
                            409, "INSUFFICIENT_INVENTORY", str(err), request_id
                        )
                    except IdempotencyConflictError as err:
                        return self._error_response(
                            409, "IDEMPOTENCY_CONFLICT", str(err), request_id
                        )
                    except DomainError as err:
                        return self._error_response(400, err.code, str(err), request_id)

                    return 200, {
                        "action_id": res.action_id,
                        "status": res.status.value,
                        "decision": res.decision.value,
                        "order_id": res.order.order_id if res.order else None,
                        "payment_id": res.payment.attempt_id if res.payment else None,
                        "approval_id": approval_id,
                        "rejection_reason": res.rejection_reason,
                        "trace_id": trace_id,
                    }

                if cred.role is None:
                    return self._error_response(
                        403,
                        "AGENT_CANNOT_APPROVE",
                        f"Agent '{cred.principal_id}' cannot grant or reject human approvals (§2.1, §20, AP-009)",
                        request_id,
                        details={"principal_id": cred.principal_id, "role": "agent"},
                    )

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
            if self.is_emergency_paused_for(tenant_id):
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
                    trace_id=trace_id,
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
            acp_dict = acp_resp.to_dict()
            acp_dict["trace_id"] = trace_id
            return 200, acp_dict

        if (
            path in ("/api/v1/protocols/ap2/mandates", "/api/v1/protocols/ap2/payments")
            and method == "POST"
        ):
            if self.is_emergency_paused_for(tenant_id):
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
                    trace_id=trace_id,
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
            ap2_dict = ap2_resp.to_dict()
            ap2_dict["trace_id"] = trace_id
            return 200, ap2_dict

        if (
            path in ("/api/v1/protocols/uap/rpc", "/api/v1/protocols/uap/messages")
            and method == "POST"
        ):
            if self.is_emergency_paused_for(tenant_id):
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
                    trace_id=trace_id,
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
            uap_dict = uap_resp.to_dict()
            uap_dict["trace_id"] = trace_id
            return 200, uap_dict

        if (
            path in ("/api/v1/protocols/x402/pay", "/api/v1/protocols/x402/challenge")
            and method == "POST"
        ):
            if self.is_emergency_paused_for(tenant_id):
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
                    trace_id=trace_id,
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
            x402_dict = x402_resp.to_dict()
            x402_dict["trace_id"] = trace_id
            return 200, x402_dict

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

        resp_headers_raw: list[list[bytes]] = [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(resp_bytes)).encode("latin1")],
        ]
        for hk, hv in self.last_response_headers.items():
            resp_headers_raw.append([hk.lower().encode("latin1"), str(hv).encode("latin1")])

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": resp_headers_raw,
            }
        )
        await send({"type": "http.response.body", "body": resp_bytes})


_DEFAULT_ROLE_SENTINEL = object()


class UnconfiguredClientAuthError(RuntimeError):
    """Raised when ApiTestClient makes a request requiring authentication without an explicit caller persona or credentials (§14)."""


class ApiTestClient:
    """Fast, synchronous client for testing the AgentPay HTTP API with explicit test authentication."""

    def __init__(
        self,
        app: AgentPayApiApp,
        default_tenant: str = "tenant_01",
        default_role: UserRole | None = None,
        auth_mode: str = "unconfigured",
    ) -> None:
        self.app = app
        self.default_tenant = default_tenant
        self.default_role = default_role
        self._tokens_by_tenant: dict[str, str] = {}
        self._api_keys_by_tenant: dict[str, str] = {}
        self._auth_mode: str = auth_mode
        self._active_principal: str | None = None
        self._active_role: UserRole | None = default_role

    def _clone(self) -> ApiTestClient:
        """Create an immutable context clone sharing app and token caches."""
        clone = ApiTestClient(
            app=self.app,
            default_tenant=self.default_tenant,
            default_role=self.default_role,
            auth_mode=self._auth_mode,
        )
        clone._tokens_by_tenant = self._tokens_by_tenant
        clone._api_keys_by_tenant = self._api_keys_by_tenant
        clone._active_principal = self._active_principal
        clone._active_role = self._active_role
        return clone

    def as_admin(self, tenant_id: str | None = None) -> ApiTestClient:
        """Return a client context configured with valid administrator credentials (§14, §67)."""
        clone = self._clone()
        clone.default_tenant = tenant_id or self.default_tenant
        clone._auth_mode = "admin"
        clone.default_role = UserRole.ADMIN
        clone._active_principal = "usr_test_admin"
        clone._active_role = UserRole.ADMIN
        return clone

    def as_agent(
        self,
        agent_id: str = "agent_01",
        tenant_id: str | None = None,
    ) -> ApiTestClient:
        """Return a client context configured with valid agent credentials (§14, §15)."""
        clone = self._clone()
        clone.default_tenant = tenant_id or self.default_tenant
        clone._auth_mode = "agent"
        clone.default_role = None
        clone._active_principal = agent_id
        clone._active_role = None
        return clone

    def as_unauthenticated(self) -> ApiTestClient:
        """Return a client context with credential auto-injection explicitly disabled (§14)."""
        clone = self._clone()
        clone._auth_mode = "unauthenticated"
        clone.default_role = None
        clone._active_principal = None
        clone._active_role = None
        return clone

    def as_buyer(self, tenant_id: str | None = None) -> ApiTestClient:
        """Return a client context configured with standard buyer credentials (§14)."""
        clone = self._clone()
        clone.default_tenant = tenant_id or self.default_tenant
        clone._auth_mode = "buyer"
        clone.default_role = UserRole.BUYER
        clone._active_principal = "usr_test_buyer"
        clone._active_role = UserRole.BUYER
        return clone

    def create_token(
        self,
        tenant_id: str = "tenant_01",
        principal_id: str | None = None,
        role: UserRole | None = UserRole.BUYER,
        status: CredentialStatus = CredentialStatus.ACTIVE,
        expires_at: datetime | None = None,
        scopes: tuple[str, ...] | None = None,
        raw_token: str | None = None,
    ) -> str:
        """Create and store a Bearer token credential in the app's credential repository."""
        if role in (UserRole.ADMIN, UserRole.AGENT_OPERATOR):
            effective_principal = principal_id or "usr_test_admin"
            effective_scopes = scopes or ("admin", "operator", "commerce:read", "commerce:write")
        elif role is None:
            effective_principal = principal_id or "agent_01"
            effective_scopes = scopes or ("agent", "commerce:read", "commerce:write")
        else:
            effective_principal = principal_id or "usr_test_buyer"
            effective_scopes = scopes or ("commerce:read", "commerce:write")

        token = raw_token or f"test_bearer_{tenant_id}_{uuid.uuid4().hex[:8]}"
        secret_hash = Credential.hash_secret(token)
        cred_id = f"cred_{uuid.uuid4().hex[:8]}"
        exp = expires_at or datetime(2099, 1, 1, tzinfo=UTC)

        cred = Credential(
            credential_id=cred_id,
            tenant_id=tenant_id,
            principal_id=effective_principal,
            credential_type=CredentialType.BEARER_TOKEN,
            secret_hash=secret_hash,
            expires_at=exp,
            status=status,
            role=role,
            scopes=effective_scopes,
        )
        if hasattr(self.app, "credential_repo") and self.app.credential_repo is not None:
            self.app.credential_repo.save(tenant_id, cred)
        return token

    def create_api_key(
        self,
        tenant_id: str = "tenant_01",
        principal_id: str | None = None,
        role: UserRole | None = UserRole.BUYER,
        status: CredentialStatus = CredentialStatus.ACTIVE,
        expires_at: datetime | None = None,
        scopes: tuple[str, ...] | None = None,
        raw_key: str | None = None,
    ) -> str:
        """Create and store an API key credential in the app's credential repository."""
        if role in (UserRole.ADMIN, UserRole.AGENT_OPERATOR):
            effective_principal = principal_id or "usr_test_admin"
            effective_scopes = scopes or ("admin", "operator", "commerce:read", "commerce:write")
        elif role is None:
            effective_principal = principal_id or "agent_01"
            effective_scopes = scopes or ("agent", "commerce:read", "commerce:write")
        else:
            effective_principal = principal_id or "usr_test_buyer"
            effective_scopes = scopes or ("commerce:read", "commerce:write")

        key = raw_key or f"apk_{tenant_id}_{uuid.uuid4().hex[:12]}"
        secret_hash = Credential.hash_secret(key)
        cred_id = f"cred_apk_{uuid.uuid4().hex[:8]}"
        exp = expires_at or datetime(2099, 1, 1, tzinfo=UTC)

        cred = Credential(
            credential_id=cred_id,
            tenant_id=tenant_id,
            principal_id=effective_principal,
            credential_type=CredentialType.API_KEY,
            secret_hash=secret_hash,
            expires_at=exp,
            status=status,
            role=role,
            scopes=effective_scopes,
        )
        if hasattr(self.app, "credential_repo") and self.app.credential_repo is not None:
            self.app.credential_repo.save(tenant_id, cred)
        return key

    def get_or_create_token(
        self,
        tenant_id: str,
        role: UserRole | object | None = _DEFAULT_ROLE_SENTINEL,
        principal_id: str | None = None,
        scopes: tuple[str, ...] | None = None,
    ) -> str:
        """Retrieve cached token or provision a new one for the given tenant, principal, and role."""
        effective_role: UserRole | None
        if role is _DEFAULT_ROLE_SENTINEL:
            effective_role = self.default_role
        else:
            if role is not None and not isinstance(role, UserRole):
                raise TypeError(f"Expected UserRole or None, got {type(role).__name__}")
            effective_role = role

        if effective_role in (UserRole.ADMIN, UserRole.AGENT_OPERATOR):
            pid = principal_id or "usr_test_admin"
            eff_scopes = scopes or ("admin", "operator", "commerce:read", "commerce:write")
        elif effective_role is None:
            pid = principal_id or "agent_01"
            eff_scopes = scopes or ("agent", "commerce:read", "commerce:write")
        else:
            pid = principal_id or "usr_test_buyer"
            eff_scopes = scopes or ("commerce:read", "commerce:write")

        role_key = effective_role.value if effective_role is not None else "none"
        key = f"{tenant_id}:{pid}:{role_key}"
        if key not in self._tokens_by_tenant:
            self._tokens_by_tenant[key] = self.create_token(
                tenant_id=tenant_id,
                principal_id=pid,
                role=effective_role,
                scopes=eff_scopes,
            )
        return self._tokens_by_tenant[key]

    @property
    def last_response_headers(self) -> dict[str, str]:
        """Return response headers from the most recent request."""
        return self.app.last_response_headers

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        auth: bool = True,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}

        require_obs_auth = getattr(self.app, "require_observability_auth", False)
        is_public_path = path in (
            "/api/v1/health",
            "/openapi.json",
            "/api/v1/webhooks/payments",
            "/api/v1/webhooks/razorpay",
            "/api/v1/webhooks/stripe",
        ) or (not require_obs_auth and path in ("/api/v1/metrics", "/api/v1/security/sbom"))

        # Explicit authentication management (§14)
        if auth and not is_public_path:
            has_explicit_auth = "authorization" in hdrs or "x-api-key" in hdrs
            if not has_explicit_auth:
                if self._auth_mode == "unauthenticated":
                    pass  # Explicitly stripped credentials; hits endpoint unauthenticated
                elif self._auth_mode == "admin":
                    target_tenant = (
                        hdrs.get("x-tenant-id")
                        or self.app._extract_protocol_tenant(path, json_body)
                        or self.default_tenant
                    )
                    token = self.get_or_create_token(
                        tenant_id=target_tenant,
                        role=UserRole.ADMIN,
                        principal_id=self._active_principal or "usr_test_admin",
                        scopes=("admin", "operator", "commerce:read", "commerce:write"),
                    )
                    hdrs["authorization"] = f"Bearer {token}"
                elif self._auth_mode == "agent":
                    target_tenant = (
                        hdrs.get("x-tenant-id")
                        or self.app._extract_protocol_tenant(path, json_body)
                        or self.default_tenant
                    )
                    token = self.get_or_create_token(
                        tenant_id=target_tenant,
                        role=None,
                        principal_id=self._active_principal or "agent_01",
                        scopes=("agent", "commerce:read", "commerce:write"),
                    )
                    hdrs["authorization"] = f"Bearer {token}"
                elif self._auth_mode in ("buyer", "default"):
                    target_tenant = (
                        hdrs.get("x-tenant-id")
                        or self.app._extract_protocol_tenant(path, json_body)
                        or self.default_tenant
                    )
                    token = self.get_or_create_token(
                        tenant_id=target_tenant,
                        role=UserRole.BUYER,
                        principal_id=self._active_principal or "usr_test_buyer",
                        scopes=("commerce:read", "commerce:write"),
                    )
                    hdrs["authorization"] = f"Bearer {token}"
                elif self._auth_mode == "unconfigured":
                    raise UnconfiguredClientAuthError(
                        f"ApiTestClient requires explicit caller persona on protected endpoint '{path}'. "
                        "Use .as_buyer(), .as_admin(), .as_agent(), or .as_unauthenticated(), "
                        "or supply an explicit Authorization or X-API-Key header (§14)."
                    )

        return self.app.handle_request(method, path, hdrs, json_body, raw_body=raw_body)

    def request_with_headers(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        auth: bool = True,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        resp = self.request(
            method, path, headers=headers, json_body=json_body, auth=auth, raw_body=raw_body
        )
        return resp.status_code, resp.body, resp.headers

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> ApiResponse:
        return self.request("GET", path, headers=headers, auth=auth)

    def post(
        self,
        path: str,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> ApiResponse:
        return self.request("POST", path, headers=headers, json_body=json_body, auth=auth)

    def post_webhook(
        self,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str = "tenant_01",
        secret: str | None = None,
        timestamp: int | None = None,
        custom_signature: str | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        """Submit a signed provider payment webhook event (§61)."""
        full_payload = dict(payload)
        if "event_type" not in full_payload:
            full_payload["event_type"] = event_type
        if "tenant_id" not in full_payload and tenant_id:
            full_payload["tenant_id"] = tenant_id

        ts_int = timestamp if timestamp is not None else int(datetime.now(UTC).timestamp())
        raw_json = json.dumps(full_payload, separators=(",", ":"), sort_keys=True)
        canonical = f"{ts_int}.{raw_json}"

        effective_secret = (
            secret
            if secret is not None
            else getattr(self.app, "webhook_secret", "whsec_test_secret_32bytes_minimum_length")
        )

        hdrs: dict[str, str] = {}
        if tenant_id:
            hdrs["x-tenant-id"] = tenant_id
        hdrs["x-webhook-timestamp"] = str(ts_int)

        if custom_signature == "__OMIT__":
            pass  # omit signature header for testing missing header
        elif custom_signature is not None:
            hdrs["x-webhook-signature"] = custom_signature
        else:
            if raw_body is not None:
                hdrs["x-webhook-signature"] = hmac.new(
                    effective_secret.encode("utf-8"),
                    raw_body,
                    hashlib.sha256,
                ).hexdigest()
            else:
                hdrs["x-webhook-signature"] = hmac.new(
                    effective_secret.encode("utf-8"),
                    canonical.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

        if headers:
            hdrs.update(headers)

        return self.request(
            "POST",
            "/api/v1/webhooks/payments",
            headers=hdrs,
            json_body=full_payload,
            auth=False,
            raw_body=raw_body,
        )

    def post_razorpay_webhook(
        self,
        payload: dict[str, Any] | bytes,
        secret: str | None = None,
        custom_signature: str | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        """Submit a signed Razorpay webhook event with X-Razorpay-Signature (§61)."""
        effective_secret = (
            secret
            if secret is not None
            else getattr(
                self.app,
                "razorpay_webhook_secret",
                getattr(self.app, "webhook_secret", "whsec_test_secret_32bytes_minimum_length"),
            )
        )
        body_bytes: bytes
        json_body: Any
        if isinstance(payload, bytes):
            body_bytes = payload
            try:
                json_body = json.loads(payload.decode("utf-8")) if payload else None
            except Exception:
                json_body = None
        else:
            json_body = payload
            body_bytes = (
                raw_body
                if raw_body is not None
                else json.dumps(payload, separators=(",", ":")).encode("utf-8")
            )

        hdrs: dict[str, str] = {}
        if custom_signature == "__OMIT__":
            pass
        elif custom_signature is not None:
            hdrs["x-razorpay-signature"] = custom_signature
        else:
            hdrs["x-razorpay-signature"] = hmac.new(
                effective_secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()

        if headers:
            hdrs.update(headers)

        return self.request(
            "POST",
            "/api/v1/webhooks/razorpay",
            headers=hdrs,
            json_body=json_body,
            auth=False,
            raw_body=body_bytes,
        )

    def post_stripe_webhook(
        self,
        payload: dict[str, Any] | bytes,
        secret: str | None = None,
        timestamp: int | None = None,
        custom_signature: str | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        """Submit a signed Stripe webhook event with Stripe-Signature (§61)."""
        effective_secret = (
            secret
            if secret is not None
            else getattr(
                self.app,
                "stripe_webhook_secret",
                getattr(self.app, "webhook_secret", "whsec_test_secret_32bytes_minimum_length"),
            )
        )
        body_bytes: bytes
        json_body: Any
        if isinstance(payload, bytes):
            body_bytes = payload
            try:
                json_body = json.loads(payload.decode("utf-8")) if payload else None
            except Exception:
                json_body = None
        else:
            json_body = payload
            body_bytes = (
                raw_body
                if raw_body is not None
                else json.dumps(payload, separators=(",", ":")).encode("utf-8")
            )

        ts_int = timestamp if timestamp is not None else int(datetime.now(UTC).timestamp())
        signed_bytes = f"{ts_int}.".encode() + body_bytes

        hdrs: dict[str, str] = {}
        if custom_signature == "__OMIT__":
            pass
        elif custom_signature is not None:
            hdrs["stripe-signature"] = custom_signature
        else:
            sig = hmac.new(
                effective_secret.encode("utf-8"),
                signed_bytes,
                hashlib.sha256,
            ).hexdigest()
            hdrs["stripe-signature"] = f"t={ts_int},v1={sig}"

        if headers:
            hdrs.update(headers)

        return self.request(
            "POST",
            "/api/v1/webhooks/stripe",
            headers=hdrs,
            json_body=json_body,
            auth=False,
            raw_body=body_bytes,
        )

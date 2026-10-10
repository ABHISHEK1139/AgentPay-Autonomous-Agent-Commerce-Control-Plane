"""Thin Developer SDK Client for AgentPay (§109).

Exposes typed interfaces for ActionRequest submission, approval status polling,
product catalog discovery, and emergency kill switch controls. Supports both
in-process application dispatch (for fast, hermetic execution) and remote HTTP endpoints.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentpay.api.app import AgentPayApiApp


class AgentPayClientError(Exception):
    """Raised when the AgentPay API returns a non-2xx error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        request_id: str | None = None,
        trace_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message} (HTTP {status_code})")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.trace_id = trace_id
        self.details = details or {}


class ActionsClient:
    """Client for submitting and querying ActionRequests (§6, §38)."""

    def __init__(self, client: AgentPay) -> None:
        self._client = client

    def request_purchase(
        self,
        product_id: str,
        variant_id: str,
        amount_minor: int,
        currency: str = "INR",
        action_id: str | None = None,
        principal_id: str = "user_01",
        agent_id: str = "agent_01",
        intent_purpose: str = "Purchase requested via AgentPay SDK",
        quantity: int = 1,
        idempotency_key: str | None = None,
        authorization_mode: str = "AUTONOMOUS",
    ) -> dict[str, Any]:
        """Submit an autonomous purchase ActionRequest (§6, §109)."""
        act_id = action_id or f"act_{uuid.uuid4().hex[:12]}"
        idem_key = idempotency_key or f"idem_{uuid.uuid4().hex[:12]}"
        payload = {
            "action_id": act_id,
            "principal_id": principal_id,
            "agent_id": agent_id,
            "domain": "commerce",
            "action": "purchase",
            "resource_type": "product_variant",
            "resource_id": product_id,
            "variant_id": variant_id,
            "quantity": quantity,
            "amount_minor": amount_minor,
            "currency": currency,
            "intent_purpose": intent_purpose,
            "idempotency_key": idem_key,
            "authorization_mode": authorization_mode,
        }
        return self._client._post("/api/v1/actions", payload)


class CatalogClient:
    """Client for product discovery and inventory inspection (§30, §38)."""

    def __init__(self, client: AgentPay) -> None:
        self._client = client

    def list_products(self) -> dict[str, Any]:
        """List products available in the tenant catalog."""
        return self._client._get("/agent/catalog")

    def get_product(self, product_id: str) -> dict[str, Any]:
        """Get full details and specifications for a product."""
        return self._client._get(f"/agent/products/{product_id}")

    def get_inventory(self, variant_id: str) -> dict[str, Any]:
        """Get live inventory stock for a product variant."""
        return self._client._get(f"/agent/products/{variant_id}/inventory")


class ApprovalsClient:
    """Client for human approval workflow inspection and decisions (§20, §66)."""

    def __init__(self, client: AgentPay) -> None:
        self._client = client

    def list_pending(self) -> list[dict[str, Any]]:
        """List pending approvals awaiting human decision."""
        res = self._client._get("/api/v1/approvals")
        approvals = res.get("approvals", [])
        if isinstance(approvals, list):
            return approvals
        return []

    def grant(self, approval_id: str, approver_id: str) -> dict[str, Any]:
        """Grant approval for a pending action."""
        payload = {"approver_id": approver_id}
        return self._client._post(f"/api/v1/approvals/{approval_id}/grant", payload)

    def reject(self, approval_id: str, approver_id: str, reason: str = "") -> dict[str, Any]:
        """Reject a pending approval."""
        payload = {"approver_id": approver_id, "reason": reason}
        return self._client._post(f"/api/v1/approvals/{approval_id}/reject", payload)


class EmergencyClient:
    """Client for emergency kill switch controls (§21, §67)."""

    def __init__(self, client: AgentPay) -> None:
        self._client = client

    def get_status(self) -> dict[str, Any]:
        """Query current emergency pause status."""
        return self._client._get("/api/v1/emergency/status")

    def pause(
        self,
        operator_id: str,
        reason: str,
        scope: str = "ALL_AUTONOMOUS_PAYMENTS",
    ) -> dict[str, Any]:
        """Trigger cluster-wide emergency pause on autonomous payments."""
        payload = {"operator_id": operator_id, "reason": reason, "scope": scope}
        return self._client._post("/api/v1/emergency/pause", payload)

    def resume(self, operator_id: str) -> dict[str, Any]:
        """Resume normal autonomous operations."""
        payload = {"operator_id": operator_id}
        return self._client._post("/api/v1/emergency/resume", payload)


class AgentPay:
    """Thin developer SDK client for the AgentPay platform (§109).

    Can be instantiated with an in-process AgentPayApiApp for fast, hermetic local
    testing, or with base_url and credentials for remote network execution.
    """

    def __init__(
        self,
        api_key: str | None = None,
        bearer_token: str | None = None,
        tenant_id: str = "tenant_01",
        base_url: str = "http://localhost:8000",
        app: AgentPayApiApp | None = None,
    ) -> None:
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.tenant_id = tenant_id
        self.base_url = base_url.rstrip("/")
        self._app = app

        self.actions = ActionsClient(self)
        self.catalog = CatalogClient(self)
        self.approvals = ApprovalsClient(self)
        self.emergency = EmergencyClient(self)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Tenant-ID": self.tenant_id,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _handle_response(self, status_code: int, body: dict[str, Any]) -> dict[str, Any]:
        if 200 <= status_code < 300:
            return body

        error_data = body.get("error", {})
        code = error_data.get("code", "UNKNOWN_ERROR")
        message = error_data.get("message", "API request failed")
        req_id = error_data.get("request_id")
        trace_id = error_data.get("trace_id")
        details = error_data.get("details", {})
        raise AgentPayClientError(
            code=code,
            message=message,
            status_code=status_code,
            request_id=req_id,
            trace_id=trace_id,
            details=details,
        )

    def _get(self, path: str) -> dict[str, Any]:
        headers = self._build_headers()
        if self._app is not None:
            status, body = self._app.handle_request("GET", path, headers)
            return self._handle_response(status, body)

        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                status_code = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return self._handle_response(status_code, body)
        except urllib.error.HTTPError as err:
            err_body = json.loads(err.read().decode("utf-8"))
            return self._handle_response(err.code, err_body)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._build_headers()
        if self._app is not None:
            status, body = self._app.handle_request("POST", path, headers, payload)
            return self._handle_response(status, body)

        url = f"{self.base_url}{path}"
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                status_code = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return self._handle_response(status_code, body)
        except urllib.error.HTTPError as err:
            err_body = json.loads(err.read().decode("utf-8"))
            return self._handle_response(err.code, err_body)

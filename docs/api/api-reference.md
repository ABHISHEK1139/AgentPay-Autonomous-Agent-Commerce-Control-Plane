# AgentPay API Reference & Contract Specification (§13, §38)

> **Document Status**: Production API Reference  
> **Governing Specifications**: `AGENTS.md` (§13 API Design & Envelopes, §14 Authentication, §21 Kill Switch, §38 Agent Commerce API, §47 Tracing)  
> **API Version**: `1.0.0` (OpenAPI 3.2.0 compliant)
> **Last Updated**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Global Conventions & Protocols

### 1.1 Base URL & Content Negotiation
- **Base URL**: `http://localhost:8000` (or `https://api.agentpay.com`)
- **Headers**:
  - `Content-Type: application/json`
  - `Accept: application/json`
  - `X-Tenant-ID: <tenant_id>` (Mandatory on all tenant-partitioned routes)
  - `X-Request-ID: <req_id>` (Optional; generated if omitted)
  - `X-Trace-ID: <trace_id>` (Optional; W3C `traceparent` supported for distributed tracing)

### 1.2 Authentication (§14)
All routes except `/api/v1/health` and `/openapi.json` require valid credentials:
- **Bearer Token**: `Authorization: Bearer <jwt_or_opaque_token>`
- **API Key**: `X-API-Key: <api_key>`
The credential's bound `tenant_id` must match the asserted `X-Tenant-ID` header; mismatches return HTTP 403 `CROSS_TENANT_ACCESS_DENIED`.

---

## 2. Standardized Error Envelopes (§13, §47)

All non-2xx responses adhere to the standard RFC error format:

```json
{
  "error": {
    "code": "POLICY_DENIED",
    "message": "Transaction amount exceeds daily agent spending limit",
    "request_id": "req_8819a001",
    "trace_id": "trace_9901ef44",
    "details": {
      "limit_minor_units": 500000,
      "requested_minor_units": 750000
    }
  }
}
```

### Common Error Codes
| HTTP Status | Error Code | Description |
|---|---|---|
| **400** | `MISSING_TENANT_HEADER` | Request omitted mandatory `X-Tenant-ID` header. |
| **400** | `INVALID_PAYLOAD` | Request body failed JSON validation or schema rules. |
| **401** | `UNAUTHENTICATED` | Missing, expired, or invalid credentials. |
| **403** | `CROSS_TENANT_ACCESS_DENIED` | Credential tenant does not match asserted tenant. |
| **403** | `UNAUTHORIZED_OPERATOR` | Caller lacks administrative privileges for emergency route. |
| **404** | `PRODUCT_NOT_FOUND` | Specified catalog product or variant does not exist. |
| **409** | `IDEMPOTENCY_CONFLICT` | Reused idempotency key with modified request parameters. |
| **422** | `INSUFFICIENT_INVENTORY` | Available stock is less than requested quantity. |
| **429** | `RATE_LIMIT_EXCEEDED` | Request threshold tripped for endpoint or tenant tier. |
| **503** | `EMERGENCY_PAUSE_ACTIVE` | Autonomous transactions paused by emergency kill switch. |

---

## 3. Core API Endpoints

### 3.1 System & Discovery
- `GET /api/v1/health`: Cluster health and kill switch state (Unauthenticated).
- `GET /openapi.json`: Machine-readable OpenAPI 3.2 schema (Unauthenticated).

### 3.2 Agent Commerce & Discovery (§38)
- `GET /agent/catalog`: List available products and variants for tenant.
- `GET /agent/products/{product_id}`: Retrieve detailed specifications and merchant metadata.
- `GET /agent/products/{variant_id}/inventory`: Real-time stock availability check.

### 3.3 Transaction Orchestration (§6, §38)
- `POST /api/v1/actions`: Submit an `ActionRequest` for policy evaluation and execution.
  - Request Body:
    ```json
    {
      "action_id": "act_01",
      "principal_id": "usr_01",
      "agent_id": "ag_01",
      "domain": "commerce",
      "action": "purchase",
      "resource_type": "product_variant",
      "resource_id": "prod_01",
      "variant_id": "var_01",
      "quantity": 1,
      "amount_minor": 149900,
      "currency": "INR",
      "intent_purpose": "laptop purchase",
      "idempotency_key": "idem_123"
    }
    ```

### 3.4 Human Approval Management (§20, §66)
- `GET /api/v1/approvals`: List pending approvals for tenant.
- `POST /api/v1/approvals/{approval_id}/grant`: Grant human approval with exact binding verification.
- `POST /api/v1/approvals/{approval_id}/reject`: Reject approval with reason.

### 3.5 Emergency Kill Switch (§21, §67)
- `GET /api/v1/emergency/status`: Query current pause state.
- `POST /api/v1/emergency/pause`: Engage global autonomous transaction freeze (`UserRole.AGENT_OPERATOR` or `ADMIN` required).
- `POST /api/v1/emergency/resume`: Resume normal autonomous operations.

### 3.6 Protocol Gateways (§107, §108)
- `POST /api/v1/protocols/acp/checkout`: Agent Commerce Protocol bridge.
- `POST /api/v1/protocols/ap2/mandates`: AP2 Mandate Settlement bridge.
- `POST /api/v1/protocols/uap/rpc`: Universal Agent Protocol JSON-RPC bridge.
- `POST /api/v1/protocols/x402/pay`: HTTP 402 Payment Required challenge bridge.

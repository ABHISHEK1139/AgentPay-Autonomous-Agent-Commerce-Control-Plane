# Razorpay Sandbox Integration Runbook

> **Document Status**: Operational Guide & Runbook  
> **Governing Specifications**: `AgentPay_Implementation_Remediation_Report.md` (AP-004, AP-017, §4 Deliverable 3), `AGENTS.md` (§39, §43, §151)  
> **Last Updated**: Remediation Sprint (2026-10-12)

---

## 1. Overview

AgentPay connects to payment gateway sandbox APIs (e.g. Razorpay Sandbox) to execute realistic payment orders, authoritative status queries, refund processing, and webhook reconciliation without exposing real money.

> [!IMPORTANT]
> **Production Financial Invariant (§151)**: AgentPay operates strictly in `mock` or `sandbox` modes. Live-money payment capture is disabled. Never use production credentials (`rzp_live_...`).

---

## 2. Prerequisites & Credentials Setup

1. **Create Razorpay Test Account**:
   - Register at [dashboard.razorpay.com](https://dashboard.razorpay.com).
   - Ensure the account is toggled to **Test Mode** (orange indicator in dashboard).
2. **Generate API Keys**:
   - Navigate to **Settings → API Keys → Generate Test Key**.
   - Copy `Key ID` (prefix `rzp_test_`) and `Key Secret`.
3. **Configure Webhook Endpoint**:
   - Navigate to **Settings → Webhooks → Add New Webhook**.
   - **URL**: `https://<your-ingress-domain>/api/v1/webhooks/payments`
   - **Secret**: Enter a cryptographically strong webhook secret.
   - **Active Events**:
     - `payment.authorized`
     - `payment.captured`
     - `payment.failed`
     - `order.paid`
     - `refund.processed`

---

## 3. Environment Configuration & Startup

Configure environment variables via shell or secret manager:

```powershell
# Set Sandbox Mode and Razorpay Provider
# (Both "razorpay" and "razorpay_sandbox" are accepted; "razorpay" normalizes to "razorpay_sandbox" in sandbox mode)
$env:AGENTPAY_ENVIRONMENT="sandbox"
$env:AGENTPAY_PAYMENT_PROVIDER="razorpay"

# Razorpay Test Credentials (requires valid test keys from dashboard.razorpay.com)
$env:RAZORPAY_KEY_ID="rzp_test_EXAMPLE12345"
$env:RAZORPAY_KEY_SECRET="your_test_secret_here"

# Webhook Secret (RAZORPAY_WEBHOOK_SECRET or AGENTPAY_WEBHOOK_SECRET fallback)
$env:RAZORPAY_WEBHOOK_SECRET="your_webhook_secret_here"
# Note: $env:AGENTPAY_WEBHOOK_SECRET="your_webhook_secret_here" is also supported as an alias

# Database Connection (SQLite or PostgreSQL)
$env:DATABASE_URL="sqlite:///agentpay_sandbox.db"
```

### 3.2 Advisory AI Intelligence Configuration (OpenRouter / OrcaRouter)

AgentPay supports external OpenAI-compatible advisory decision routers, including **OpenRouter** (e.g. `apodex/apodex-1.1-mini:free`) and **OrcaRouter** (`orcarouter/auto`). Model evaluations are strictly advisory and sit below the deterministic policy boundary (§2.1, §23):

```powershell
# OpenRouter configuration (defaults to apodex/apodex-1.1-mini:free)
$env:AGENTPAY_DECISION_ENGINE="openrouter"
$env:OPENROUTER_API_KEY="sk-or-v1-..."

# Optional overrides:
# $env:OPENROUTER_MODEL="apodex/apodex-1.1-mini:free"
# $env:OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
```

### 3.3 Startup Health Verification

Launch AgentPay with database migrations:

```powershell
# Apply schema migrations
uv run alembic upgrade head

# Launch API server
uv run agentpay serve --port 8000
```

Verify startup diagnostics via the health probe:

```powershell
curl http://127.0.0.1:8000/api/v1/health
```

Expected response confirms sandbox mode:
```json
{
  "status": "healthy",
  "emergency_paused": false,
  "version": "1.0.0-mvp",
  "environment": "sandbox",
  "payment_provider": "razorpay_sandbox",
  "decision_engine": "mock"
}
```

---

## 4. Exercising an End-to-End Sandbox Transaction

### Step 1: Submit Autonomous Purchase

Submit an `ActionRequest` under configured spending limits:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/actions `
  -H "Authorization: Bearer test_bearer_agent_token" `
  -H "Content-Type: application/json" `
  -d '{
    "tenant_id": "tenant_01",
    "principal_id": "user_01",
    "agent_id": "agent_01",
    "domain": "commerce",
    "action": "purchase",
    "resource": {"type": "product_variant", "id": "var_01"},
    "amount": {"minor_units": 149900, "currency": "INR"},
    "intent": {"purpose": "developer hardware procurement"}
  }'
```

### Step 2: Inspect Provider Mapping

Verify that AgentPay created a Razorpay order and recorded the internal mapping:
- `provider`: `"razorpay"`
- `provider_order_id`: `"order_..."`
- `attempt_id`: Assigned internal UUID

### Step 3: Trigger Webhook Delivery (Simulation or Dashboard)

Simulate a signed Razorpay `payment.captured` webhook payload:

```powershell
# Compute HMAC-SHA256 signature over raw JSON bytes
# Send to /api/v1/webhooks/payments with header X-Razorpay-Signature: <hex_digest>
```

Verify that AgentPay:
1. Validates the signature using `hmac.compare_digest`.
2. Resolves the transaction through `provider_mappings`.
3. Records the event in `provider_webhook_events` to prevent replay.
4. Transitions order status to `COMPLETED` and decrements physical inventory.

---

## 5. Failure Injection & SAGA Compensation Testing

### Injected Decline Simulation
1. Transact using a failing card number or simulate a `payment.failed` event from Razorpay.
2. Confirm the SAGA orchestrator:
   - Sets payment attempt status to `PaymentStatus.DECLINED` or `FAILED`.
   - Automatically executes compensating action: releases reserved inventory.
   - Appends a failure event to the SHA-256 audit ledger.

### Timeout & Authoritative Query
1. Inject a network delay during payment capture.
2. Ensure AgentPay does **not** blindly retry the charge.
3. Observe `PaymentReconciler` querying `GET https://api.razorpay.com/v1/payments/<payment_id>` to read authoritative provider state.

---

## 6. Troubleshooting & Common Errors

| Error Code / Symptom | Root Cause | Remediation |
|---|---|---|
| `CONFIG_ERROR: Missing Razorpay credentials` | `RAZORPAY_KEY_ID` or `RAZORPAY_KEY_SECRET` unset in sandbox mode | Set credentials in environment before startup. |
| `INVALID_SIGNATURE` (401) | Webhook secret mismatch or body modified by proxy | Verify `AGENTPAY_WEBHOOK_SECRET` matches provider dashboard and HTTP proxy does not rewrite raw bytes. |
| `QUARANTINED: UNKNOWN_PROVIDER_TRANSACTION` | Webhook arrived for an order created outside AgentPay | Verify that order was initiated via AgentPay orchestrator so mapping exists. |
| `DUPLICATE_WEBHOOK_SKIPPED` | Webhook replayed by provider | Normal operation: durable deduplication prevented duplicate transition. |

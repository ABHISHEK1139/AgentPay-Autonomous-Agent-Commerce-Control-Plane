# Webhook Verification & Identity Mapping Specification

> **Document Status**: Production Security Standard  
> **Governing Specifications**: `AgentPay_Implementation_Remediation_Report.md` (AP-001, AP-002, AP-003, §4 Deliverable 2), `AGENTS.md` (§40, §41, §61)  
> **Last Updated**: Remediation Sprint (2026-10-12)

---

## 1. Security Architecture & Threat Model

Payment webhooks are unauthenticated external ingress paths that receive critical transaction state transitions (e.g. payment authorized, captured, failed, or refunded). An attacker who can forge or replay webhooks could manipulate financial records, trigger unauthorized inventory releases, or drain corporate budgets.

AgentPay secures webhook ingress via four interlocking layers:
1. **Raw-Byte Cryptographic Verification**: Exact provider-specific HMAC-SHA256 signature calculation over raw wire bytes before JSON parsing.
2. **Durable Database Deduplication**: Atomic event claiming in `provider_webhook_events` with unique constraints across restarts and worker replicas.
3. **Stored Identity Mapping**: Tenant and transaction attribution resolved exclusively through pre-persisted `provider_mappings`. Zero tenant fallback heuristics.
4. **SAGA State-Machine Invariants**: Idempotent transitions and safe inventory release guards protecting against duplicate or out-of-order deliveries.

---

## 2. Provider Signature Contracts

### 2.1 Razorpay Webhooks (`POST /api/v1/webhooks/payments` with `X-Razorpay-Signature`)
- **Signature Algorithm**: HMAC-SHA256
- **Signature Header**: `X-Razorpay-Signature` (hexadecimal digest)
- **Signed Payload**: The exact raw HTTP request body bytes (`bytes`).
- **Timestamp Specification**: Razorpay webhook contracts do not sign a timestamp header. Replay protection is enforced via durable event deduplication on the unique event ID (`payload["event_id"]`).
- **Constant-Time Verification**:
  ```python
  expected_signature = hmac.new(
      webhook_secret.encode("utf-8"),
      raw_body_bytes,
      hashlib.sha256,
  ).hexdigest()
  is_valid = hmac.compare_digest(expected_signature, signature_header)
  ```
- **Prohibited Behavior**: Never canonicalize or re-serialize JSON before signature verification. Any byte modification invalidates the signature.

### 2.2 Stripe Webhooks (`POST /api/v1/webhooks/payments` with `Stripe-Signature`)
- **Signature Algorithm**: HMAC-SHA256
- **Signature Header**: `Stripe-Signature` (`t=<timestamp>,v1=<sig1>[,v1=<sig2>...]`)
- **Signed Payload**: `f"{timestamp}.".encode("utf-8") + raw_body_bytes`
- **Timestamp Freshness**: Checked against current UTC time:
  ```python
  if abs(current_time_utc - signed_timestamp) > 300:
      raise StaleWebhookTimestampError("Webhook timestamp outside 300s tolerance")
  ```
- **Secret Rotation**: Iterates over all parsed `v1` signatures in the header, validating that at least one matches the current active secret (or retiring candidate secret).

---

## 3. Tenant Attribution & Identity Mapping (AP-001)

### 3.1 Strict Anti-Fallback Invariant
Under no circumstances may a webhook handler default to a fallback tenant:
```python
# FORBIDDEN (AP-001 finding):
tenant_id = notes.get("tenant_id") or "tenant_01"  # SECURITY VULNERABILITY

# REQUIRED & ENFORCED:
mapping = provider_mapping_repo.find_by_provider_payment_id(
    provider=event.provider,
    provider_payment_id=provider_payment_id,
)
if not mapping:
    # Fail closed: Quarantine event and log security audit
    return WebhookResult.quarantine(reason="UNKNOWN_PROVIDER_TRANSACTION")
tenant_id = mapping.tenant_id
```

### 3.2 Mapping Resolution Rules
1. During purchase authorization, `PurchaseOrchestrator` persists a `ProviderTransactionMapping` record storing:
   - `provider`: e.g. `"razorpay"` or `"stripe"`
   - `provider_account_id`: Merchant provider account ID
   - `provider_order_id`: e.g. `"order_IEg12345"`
   - `provider_payment_id`: e.g. `"pay_IEg67890"`
   - `internal_attempt_id`: UUID of `PaymentAttempt`
   - `tenant_id`: Authenticated tenant ID
2. Incoming webhooks parse the provider payment and order IDs from the verified payload and query `provider_mappings`.
3. If no matching mapping exists, the webhook is quarantined with HTTP 200/202 status (to prevent provider retry storms) but zero internal state mutation occurs.

---

## 4. Durable Event Deduplication (AP-003)

### 4.1 Database Model (`provider_webhook_events`)
```sql
CREATE TABLE provider_webhook_events (
    id VARCHAR(64) PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    provider_account_id VARCHAR(64) NOT NULL,
    provider_event_id VARCHAR(128) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    processing_status VARCHAR(32) NOT NULL,
    attempt_count INTEGER DEFAULT 1,
    last_error_code VARCHAR(64),
    entity_reference VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_provider_event UNIQUE (provider, provider_account_id, provider_event_id)
);
```

### 4.2 Deduplication Lifecycle
1. **Atomic Claim**: Before processing, the application attempts an atomic record insert for `(provider, provider_account_id, provider_event_id)`.
2. **Duplicate Conflict**: If a unique key collision occurs, the event is immediately flagged as `ALREADY_PROCESSED`. The business transition is skipped, and an idempotent acknowledgment is returned.
3. **Crash Recovery**: If the node crashes after claim but before state update, the event status remains `PENDING_RECONCILIATION`, allowing the background worker to inspect and resolve the transaction.

---

## 5. Webhook Secret Rotation Runbook

When rotating payment provider webhook secrets:

1. **Step 1 (Pre-Rotation)**: Generate new secret in provider dashboard (do not delete old secret).
2. **Step 2 (Configuration Update)**: Add new secret to deployment environment variables as `AGENTPAY_WEBHOOK_SECRET_NEXT`.
3. **Step 3 (Dual Verification)**: The application checks `AGENTPAY_WEBHOOK_SECRET`; if verification fails, it checks `AGENTPAY_WEBHOOK_SECRET_NEXT`.
4. **Step 4 (Promotion)**: Update `AGENTPAY_WEBHOOK_SECRET` to the new secret and remove the candidate variable.
5. **Step 5 (Revocation)**: Delete the retired secret in the provider dashboard.

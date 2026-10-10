# Merchant Suspension & Catalog Gating Runbook (§30, §37, §133, §153)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-005`  
> **Severity / SLA**: Sev 2 (MTTA ≤ 15m, MTTC ≤ 30m, MTTR ≤ 4h)  
> **Service / Module**: `agentpay.domain.commerce`, `agentpay.application.ports.commerce_port`, `agentpay.application.use_cases.purchase_flow`  
> **Author / Owner**: Merchant Operations & Fraud Prevention Team  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§30, §37, §133, §153), Vector 09, Vector 12  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay enforces strict merchant qualification, whitelisting, and real-time catalog verification to ensure autonomous agents transact only with verified, active vendors. If a merchant's catalog feed is compromised, exhibits pricing anomalies, generates counterfeit product listings, or suffers high dispute rates, operators must immediately gate and suspend the merchant.

This runbook specifies:
- The finite state machine governing merchant lifecycle and agent purchase authorization.
- Trigger conditions, price drift detection, and fraud alarms.
- Procedures for transitioning merchant status via API, PowerShell, and Python.
- Cache invalidation protocols in Redis to prevent stale quote exploitation (Vector 12).
- Fail-closed verification ensuring in-flight actions and quotes targeting the vendor are rejected.

---

## 2. Merchant Lifecycle & Gating Model (§37, §153)

AgentPay defines 5 discrete merchant states in `apps/api/src/agentpay/domain/commerce.py`:
- `REGISTERED`: Initial profile created; unverified. Autonomous agent purchases blocked.
- `VERIFIED`: KYB business identity verified; awaiting operational whitelisting. Agent purchases blocked.
- `ACTIVE`: Fully operational. Autonomous purchases permitted **only if `allowed_agent_purchase == True`**.
- `SUSPENDED`: Temporarily halted due to dispute spikes, pricing drift, or fraud investigation. Agent purchases blocked.
- `REVOKED`: Permanently banned. Terminal state; vendor cannot be re-activated.

```
                    ┌──────────────┐
                    │  REGISTERED  │
                    └──────┬───────┘
                           │ (KYB Verification)
                           ▼
                    ┌──────────────┐
                    │   VERIFIED   │
                    └──────┬───────┘
                           │ (Whitelisting: allowed_agent_purchase=True)
                           ▼
                    ┌──────────────┐
                    │    ACTIVE    │
                    └──────┬───────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
      ┌────────────┐              ┌───────────┐
      │ SUSPENDED  │              │  REVOKED  │ (Terminal)
      └─────┬──────┘              └───────────┘
            │ (Clearance)
            ▼
      ┌────────────┐
      │   ACTIVE   │
      └────────────┘
```

---

## 3. Trigger Conditions & Detection Alarms

- **Price Manipulation / Drift Alarm**: `EvidenceConflictDetector` flags recurring `ConflictType.PRICE_MISMATCH` where final checkout price exceeds original quote.
- **Inventory Discrepancy Spike**: Excessive checkout failures caused by phantom stock (`InsufficientInventoryError`).
- **Fraud / Dispute Threshold Breach**: Chargeback or fraud claim rate exceeds 1.0% of total processed transactions.
- **Counterfeit Listing Report**: Consumer or brand owner alerts regarding counterfeit or unauthorized merchandise.
- **Sanctions & Compliance Notices**: Legal freeze order or sanctions list match.

---

## 4. Diagnostic Steps

### 4.1 Search Telemetry for Price Conflicts via PowerShell
```powershell
Get-Content -Path "C:\var\log\agentpay\commerce.log" -Tail 5000 | 
    Select-String -Pattern "PRICE_MISMATCH" | 
    Select-String -Pattern "merchant_id=merch_"
```

### 4.2 Inspect Merchant Status via API
#### Using PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/merchants/merch_bad_vendor" -Method Get -Headers $headers
```

#### Using cURL:
```bash
curl -s -X GET "http://localhost:8000/api/v1/merchants/merch_bad_vendor" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: tenant_01"
```

### 4.3 Python Diagnostic Query
```python
from agentpay.adapters.in_memory.commerce_repository import InMemoryMerchantRepository

merchant_repo = InMemoryMerchantRepository()
merchant = merchant_repo.get("tenant_01", "merch_bad_vendor")
if merchant:
    print(f"Merchant: {merchant.name}, Status: {merchant.status.value}, Allowed Agent Purchase: {merchant.allowed_agent_purchase}")
```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Execute Merchant Suspension

#### Via Python Maintenance Script:
```python
from agentpay.application.ports.commerce_port import MerchantRepository
from agentpay.domain.commerce import Merchant, MerchantStatus

def suspend_merchant(
    merchant_repo: MerchantRepository,
    tenant_id: str,
    merchant_id: str,
    reason: str,
) -> None:
    merchant = merchant_repo.get(tenant_id, merchant_id)
    if merchant is None:
        raise ValueError(f"Merchant {merchant_id} not found in tenant {tenant_id}")
    
    suspended_merchant = Merchant(
        merchant_id=merchant.merchant_id,
        tenant_id=merchant.tenant_id,
        name=merchant.name,
        status=MerchantStatus.SUSPENDED,
        allowed_agent_purchase=False,
        max_order_amount_minor=merchant.max_order_amount_minor,
    )
    merchant_repo.save(tenant_id, suspended_merchant)
    print(f"Merchant {merchant_id} transitioned to SUSPENDED. Allowed agent purchases set to False. Reason: {reason}")
```

#### Via PowerShell API Call:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
$body = @{
    status                 = "SUSPENDED"
    allowed_agent_purchase = $false
    reason                 = "Price drift conflict threshold breached (>5 mismatches/hour)"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/merchants/merch_bad_vendor/status" `
    -Method Post -Headers $headers -Body $body
```

### Step 2: Invalidate Catalog & Stale Quote Cache in Redis (§51)

To prevent autonomous agents from transacting against cached quotes (Vector 12), immediately purge Redis catalog cache keys for the merchant:

#### Redis CLI (Linux / Container):
```bash
redis-cli --eval "return redis.call('del', unpack(redis.call('keys', 'tenant:' .. ARGV[1] .. ':catalog:' .. ARGV[2] .. ':*')))" 0 "tenant_01" "merch_bad_vendor"
```

#### PowerShell Redis Command:
```powershell
# Using redis-cli on Windows
& "redis-cli.exe" KEYS "tenant:tenant_01:catalog:merch_bad_vendor:*" | ForEach-Object {
    & "redis-cli.exe" DEL $_
}
```

### Step 3: Invalidation of In-Flight Actions & Approvals

The `PurchaseOrchestrator` validates merchant eligibility at both quote generation time and final checkout execution time. Any in-flight action targeting the suspended merchant immediately fails closed:

*Expected Error Response*:
```json
{
  "error": {
    "code": "MERCHANT_SUSPENDED",
    "message": "Merchant 'merch_bad_vendor' is not active for autonomous agent purchases",
    "request_id": "req_8819a001",
    "trace_id": "trace_1129aa54",
    "details": {
      "merchant_id": "merch_bad_vendor",
      "status": "SUSPENDED"
    }
  }
}
```

---

## 6. Verification & Health Restoration

1. **Verify Checkout Rejection**:
   Attempt a test purchase against the suspended merchant and confirm HTTP 400/403 `MERCHANT_SUSPENDED` is returned.
2. **Verify Peer Merchant Continuity**:
   Verify that valid purchases against other active merchants continue without latency or error.
3. **Re-activation Protocol**:
   Reinstating a merchant to `ACTIVE` requires:
   - Full dispute resolution and refund settlement.
   - Re-synchronized and validated product catalog feed.
   - Signed compliance clearance from Merchant Operations.

---

## 7. Audit Trail Accounting & Post-Mortem

Merchant status changes emit audit events in the tamper-evident ledger:
- **Event Type**: `AuditEventType.CONFLICT_DETECTED` / `SECURITY_EVENT`
- **Data Payload**:
```json
{
  "merchant_id": "merch_bad_vendor",
  "prior_status": "ACTIVE",
  "target_status": "SUSPENDED",
  "allowed_agent_purchase": false,
  "operator_id": "ops_lead_01",
  "reason": "Price drift conflict threshold breached (>5 mismatches/hour)",
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 5-Whys Post-Mortem Template
1. **Why was the merchant suspended?** Multiple transactions failed with `ConflictType.PRICE_MISMATCH`.
2. **Why did price mismatches occur?** Merchant's backend e-commerce feed modified prices without updating AgentPay's catalog cache.
3. **Why did the agent not complete the purchase?** `EvidenceConflictDetector` flagged the price discrepancy and halted checkout.
4. **Why was cache invalidation necessary?** To prevent subsequent agents from acting on cached stale quotes.
5. **How will recurrence be prevented?** Enforce webhook catalog sync from merchant backend before accepting price changes.

---

## 8. Related References & Runbooks
- [06-cross-tenant-incident-runbook.md](file:///docs/runbooks/06-cross-tenant-incident-runbook.md) — Multi-tenant data segregation.
- [07-rate-limit-abuse-mitigation-runbook.md](file:///docs/runbooks/07-rate-limit-abuse-mitigation-runbook.md) — Rate limiting abusive requests.
- [docs/operations/incident-runbook.md](file:///docs/operations/incident-runbook.md) — General incident framework.

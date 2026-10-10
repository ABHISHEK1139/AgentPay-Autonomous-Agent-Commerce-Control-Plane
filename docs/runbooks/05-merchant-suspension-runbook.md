# Merchant Suspension & Catalog Gating Runbook (§30, §37, §133, §153)

> **Document Status**: Production Standard  
> **Target Audience**: Merchant Operations, SecOps, Fraud Prevention Lead, Platform SRE  
> **Governing Specifications**: `AGENTS.md` (§30, §37, §133, §153), Vector 09, Vector 12  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Merchant Lifecycle & Gating Model (§37, §153)

AgentPay enforces rigorous merchant qualification to ensure autonomous agents never transact with fraudulent, unverified, or compromised merchants.

### 1.1 Merchant Status State Machine
- `REGISTERED`: Merchant profile created; not verified. Agent purchases blocked.
- `VERIFIED`: KYB identity verified; awaiting operational whitelisting. Agent purchases blocked.
- `ACTIVE`: Fully operational. Agent purchases permitted if `allowed_agent_purchase == True`.
- `SUSPENDED`: Temporarily halted due to dispute spikes, pricing drift, or fraud investigation. Agent purchases blocked.
- `REVOKED`: Permanently banned. Terminal state.

---

## 2. Trigger Conditions & Detection Alarms

- **Price Manipulation / Drift**: `EvidenceConflictDetector` repeatedly flags `ConflictType.PRICE_MISMATCH` where checkout price exceeds quoted price.
- **Inventory Discrepancies**: High failure rate due to phantom inventory reservations (`InsufficientInventoryError`).
- **Fraud & Counterfeit Reports**: Consumer or enterprise fraud reports regarding product delivery or card disputes (> 1% chargeback rate).
- **Sanctions & Compliance Notices**: Legal notices requiring merchant freeze.

---

## 3. Step-by-Step Merchant Suspension Protocol

### Step 1: Update Merchant Status in Repository

Transition merchant status to `SUSPENDED` and set `allowed_agent_purchase = False`:

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
    print(f"Merchant {merchant_id} transitioned to SUSPENDED. Reason: {reason}")
```

### Step 2: Invalidate Catalog & Quote Cache (§51)

To prevent agents from executing transactions against cached quotes (Vector 12), immediately purge Redis catalog cache keys:

```bash
# Redis CLI command pattern
redis-cli --eval "return redis.call('del', unpack(redis.call('keys', 'tenant:' .. ARGV[1] .. ':catalog:' .. ARGV[2] .. ':*')))" 0 "<tenant_id>" "<merchant_id>"
```

### Step 3: Invalidate In-Flight Actions & Approvals

The `PurchaseOrchestrator` performs pre-flight merchant status verification at quote evaluation, approval verification, and payment capture time. Any in-flight action targeting the suspended merchant will immediately fail closed with:
```json
{
  "error": {
    "code": "MERCHANT_SUSPENDED",
    "message": "Merchant 'merch_bad_vendor' is not active for autonomous agent purchases",
    "request_id": "req_8819a001",
    "trace_id": "trace_1129aa54"
  }
}
```

---

## 4. Re-activation Verification Protocol

A suspended merchant may only be reinstated to `ACTIVE` after:
1. Full reconciliation of all disputed transactions.
2. Verified re-synchronization of product catalog and inventory feeds.
3. Formal clearance by Merchant Operations and Fraud Prevention teams.

---

## 5. Audit Logging

Merchant status modifications emit audit records:
- **Event Type**: `AuditEventType.CONFLICT_DETECTED` / Security Event
- **Payload**: Includes merchant ID, operator ID, suspension reason, prior status, and timestamp.

---

## 6. Cross-References
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md)
- [06-cross-tenant-incident-runbook.md](file:///docs/runbooks/06-cross-tenant-incident-runbook.md)

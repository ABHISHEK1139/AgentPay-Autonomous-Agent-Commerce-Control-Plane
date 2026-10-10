# Payment Timeout & Reconciliation Runbook (§41, §42, §43, §44, §133)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-003`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 5m, MTTC ≤ 15m, MTTR ≤ 2h)  
> **Service / Module**: `agentpay.application.services.reconciliation_service`, `agentpay.domain.payments`  
> **Author / Owner**: SRE Lead & Payment Integration Team  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§41, §42, §43, §44, §133), ADR-0001, Vector 02  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

When autonomous agents initiate commerce transactions, external payment service providers (PSPs) such as Razorpay, Stripe, or banking gateways may drop TCP connections, experience gateway timeouts (HTTP 504), or crash during processing.

This runbook specifies:
- The fundamental financial non-negotiables: **never blindly retry a charge on timeout**.
- Triage and discovery procedures for payment attempts in `PaymentStatus.UNKNOWN`.
- Step-by-step automated and manual reconciliation using `PaymentReconciler`.
- The saga compensation decision matrix governing refunds and inventory release.
- Cryptographic audit trail requirements and post-incident verification.

---

## 2. Non-Negotiable Invariants (§43, §0.4)

1. **NEVER BLINDLY RETRY A PAYMENT CHARGE (§43)**:  
   Blindly retrying a payment charge upon timeout causes catastrophic duplicate debiting, customer disputes, and regulatory penalties. The platform MUST query the PSP's authoritative state before taking any action.
2. **PAYMENT STATE IS NOT ORDER STATE (§41)**:  
   Never infer payment success from an order record or client redirect. Payment capture is strictly verified via authoritative provider telemetry.
3. **READ-ONLY RECONCILIATION**:  
   The `PaymentReconciler` inspects status; it never creates new charges. It transitions `PaymentStatus.UNKNOWN` into an authoritative terminal state (`CAPTURED` or `FAILED`).

---

## 3. Trigger Conditions & Detection Alarms

- **Gateway Timeout (HTTP 504 / Socket Hangup)**: Outgoing payment capture call to external PSP drops before receiving an acknowledgment response.
- **Stuck `UNKNOWN` Attempt Alarm**: Prometheus alert fires when any `PaymentAttempt` remains in `PaymentStatus.UNKNOWN` for > 60 seconds.
- **Order-Payment State Mismatch**: Order repository contains `PENDING` order while payment provider records captured transaction.
- **Idempotency Replay Conflict**: Repeated authorization requests with identical idempotency key returning indeterminate status.

---

## 4. Diagnostic Steps

### 4.1 Search Telemetry for Stuck Attempts via PowerShell
```powershell
Get-Content -Path "C:\var\log\agentpay\payments.log" -Tail 5000 | 
    Select-String -Pattern "status=UNKNOWN" | 
    Select-String -Pattern "attempt_id="
```

### 4.2 Query Stuck Payment Attempts via Python CLI
```python
from agentpay.adapters.in_memory.payment_repository import InMemoryPaymentRepository
from agentpay.domain.payments import PaymentStatus

payment_repo = InMemoryPaymentRepository()
stuck_attempts = [
    attempt for attempt in payment_repo.list_for_tenant("tenant_01")
    if attempt.status == PaymentStatus.UNKNOWN
]
print(f"Stuck UNKNOWN attempts found: {len(stuck_attempts)}")
for att in stuck_attempts:
    print(f"  Attempt ID: {att.attempt_id}, Amount: {att.amount.minor_units} {att.amount.currency}")
```

### 4.3 Inspect PSP Gateway State directly (API)
Verify what the PSP records for the idempotency key:
```bash
curl -s -X GET "https://api.razorpay.com/v1/payments/{payment_id}" \
  -u "<api_key>:<api_secret>"
```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Automated Status Reconciliation Execution

Invoke `PaymentReconciler` to query the gateway and update local state safely:

```python
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.application.ports.payments_port import PaymentProvider, PaymentRepository

def reconcile_stuck_payment(
    payment_provider: PaymentProvider,
    payment_repo: PaymentRepository,
    tenant_id: str,
    attempt_id: str,
) -> None:
    reconciler = PaymentReconciler(payment_provider=payment_provider, payment_repo=payment_repo)
    
    # Safe read-only status query and state transition
    updated_attempt = reconciler.reconcile_unknown_payment(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
    )
    print(f"Reconciliation result for {attempt_id}: {updated_attempt.status.value}")
```

### Step 2: Saga Compensation Execution (§44)

Depending on the authoritative provider status returned, execute the corresponding saga compensation:

```
                          ┌──────────────────────────┐
                          │ PaymentStatus == UNKNOWN │
                          └────────────┬─────────────┘
                                       │
                         Query Provider State (Read-Only)
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
     Provider: CAPTURED                                    Provider: FAILED
            │                                                     │
    Check Order Record                                     Release Reserved
            │                                              Inventory Stock
     ┌──────┴──────┐                                              │
     ▼             ▼                                              ▼
Order Exists   Order Missing                             Emit PAYMENT_FAILED
     │             │                                         Audit Event
Fulfill Order  Execute Saga Refund
               (Release Inventory)
```

| Provider Status | Local Order Status | Action Required | Compensation Protocol |
|---|---|---|---|
| **CAPTURED** | Order Exists | Transition local payment to `CAPTURED`. Fulfill order. | None needed; sync records and notify customer. |
| **CAPTURED** | Order Missing / Failed | Transition local payment to `CAPTURED`. Immediately issue refund. | Call `payment_provider.refund(tenant_id, attempt_id, amount, reason="saga compensation")`. Release held inventory. |
| **FAILED** | Any | Transition local payment to `FAILED`. Release inventory. | Call `inventory_repo.release(tenant_id, variant_id, qty)`. Record payment failure. |
| **UNKNOWN** | Persistent | Escalate to human operator / PSP dashboard. | Retain locks; alert financial operations on-call. |

### Step 3: Saga Refund Invocation via Python

When order creation failed after funds were captured:

```python
def execute_saga_refund(
    payment_provider: PaymentProvider,
    inventory_repo,
    tenant_id: str,
    attempt_id: str,
    amount,
    variant_id: str,
    qty: int,
) -> None:
    # 1. Trigger provider refund
    refund = payment_provider.refund(
        tenant_id=tenant_id,
        payment_attempt_id=attempt_id,
        amount=amount,
        reason="saga compensation: order creation failed",
    )
    # 2. Release inventory stock
    inventory_repo.release(tenant_id=tenant_id, variant_id=variant_id, quantity=qty)
    print(f"Saga compensation completed: Refund ID {refund.refund_id}")
```

---

## 6. Verification & Health Restoration

After executing reconciliation:
1. **Verify Stock Counters**: Ensure `Inventory.reserved` and `Inventory.available` match true warehouse inventory counters.
2. **Confirm Zero Stray Locks**: Ensure no idempotency reservations remain hung in `IdempotencyStore`.
3. **Verify Ledger Consistency**: Recompute audit chain to ensure reconciliation records are properly committed:
```powershell
uv run python scripts/verify_audit_chain.py /var/log/agentpay/audit_events.jsonl
```

---

## 7. Audit Trail Accounting & Post-Mortem

Reconciliation emits definitive audit events:
- **`AuditEventType.PAYMENT_CAPTURED`** or **`AuditEventType.PAYMENT_FAILED`**:
```json
{
  "attempt_id": "att_9011ab44",
  "tenant_id": "tenant_01",
  "status": "CAPTURED",
  "reconciled": true,
  "reconciled_by": "PaymentReconciler",
  "amount_minor_units": 149900,
  "currency": "INR",
  "timestamp": "2026-10-10T12:05:00Z"
}
```

### 5-Whys Post-Mortem Template
1. **Why was payment reconciliation needed?** A timeout occurred during payment capture.
2. **Why did the timeout occur?** PSP network socket dropped or had high latency.
3. **Why did we not retry the charge?** System invariant §43 forbids blind retries to prevent duplicate charges.
4. **Why was reconciliation successful?** Read-only provider query established ground truth without creating duplicate debits.
5. **How will recurrence be prevented?** Tune TCP keepalive and socket timeout values; alert PSP account team regarding gateway SLA breach.

---

## 8. Related References & Runbooks
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Halting payments during widespread PSP outages.
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md) — Audit chain verification.
- [docs/operations/incident-runbook.md](file:///docs/operations/incident-runbook.md) — Incident response framework.

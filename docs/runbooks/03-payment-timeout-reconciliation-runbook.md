# Payment Timeout & Reconciliation Runbook (§41, §42, §43, §44, §133)

> **Document Status**: Production Standard  
> **Target Audience**: SRE Lead, Financial Operations Engineers, Payment Integration Team  
> **Governing Specifications**: `AGENTS.md` (§41, §42, §43, §44, §133), ADR-0001, Vector 02  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Non-Negotiable Invariants (§43, §0.4)

When communicating with external payment service providers (PSPs) such as Razorpay, Stripe, or bank gateways, network sockets may drop, HTTP 504 timeouts may occur, or database locks may stall execution.

1. **NEVER BLINDLY RETRY A PAYMENT CHARGE (§43)**:  
   Blindly retrying a payment charge upon timeout causes catastrophic duplicate debiting and chargeback penalties. The platform must query the provider's authoritative state before taking any action.
2. **PAYMENT STATE IS NOT ORDER STATE (§41)**:  
   Never infer payment success from an order record or client redirect. Payment capture is strictly verified via provider telemetry.
3. **READ-ONLY RECONCILIATION**:  
   The `PaymentReconciler` inspects status; it never creates new charges. It transitions `PaymentStatus.UNKNOWN` into an authoritative terminal state (`CAPTURED` or `FAILED`).

---

## 2. Trigger Scenarios & Detection Alerts

- **Gateway Timeout (HTTP 504 / Connection Reset)**: Payment request to external PSP drops before receiving an acknowledgment response.
- **Stuck `UNKNOWN` Attempt**: Alert fires when `PaymentAttempt.status == PaymentStatus.UNKNOWN` for more than 60 seconds.
- **Idempotency Replay Anomaly**: Repeated authorization requests with identical idempotency key returning pending status.

---

## 3. Step-by-Step Triage & Automated Reconciliation

### Step 1: Query Stuck Payment Attempts

Identify all payment attempts in `UNKNOWN` state within the target tenant:

```python
from agentpay.application.ports.payments_port import PaymentRepository
from agentpay.domain.payments import PaymentStatus

def get_stuck_attempts(payment_repo: PaymentRepository, tenant_id: str):
    return [
        attempt for attempt in payment_repo.list_for_tenant(tenant_id)
        if attempt.status == PaymentStatus.UNKNOWN
    ]
```

### Step 2: Execute Reconciler Inspection

Invoke `PaymentReconciler` to query the gateway and update state safely:

```python
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.application.ports.payments_port import PaymentProvider, PaymentRepository

def reconcile_stuck_payment(
    reconciler: PaymentReconciler,
    tenant_id: str,
    attempt_id: str,
) -> None:
    # Safe read-only status refresh
    updated_attempt = reconciler.reconcile_unknown_payment(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
    )
    print(f"Reconciliation completed: {attempt_id} -> {updated_attempt.status}")
```

---

## 4. Saga Compensation Decision Matrix (§44)

Depending on what the provider reports, execute the appropriate saga compensation:

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
| **CAPTURED** | Order Exists | Transition local payment to `CAPTURED`. Fulfill order. | None needed; sync records. |
| **CAPTURED** | Order Missing / Failed | Transition local payment to `CAPTURED`. Immediately issue refund. | Call `payment_provider.refund(tenant_id, attempt_id, amount, reason="saga compensation")`. Release inventory. |
| **FAILED** | Any | Transition local payment to `FAILED`. Release inventory. | Call `inventory_repo.release(tenant_id, variant_id, qty)`. Record payment failure. |
| **UNKNOWN** | Persistent | Escalate to human operator / PSP dashboard. | Retain locks; alert financial operations on-call. |

---

## 5. Verification & Health Restoration

After executing reconciliation:
1. **Verify Stock Counters**: Ensure `Inventory.reserved` and `Inventory.available` match true warehouse inventory.
2. **Confirm Zero Stray Locks**: Ensure no idempotency reservations remain hung.
3. **Ledger Consistency**: Recompute audit chain to ensure reconciliation records are properly committed.

---

## 6. Audit Trail Logging

Reconciliation emits definitive audit events:
- **`AuditEventType.PAYMENT_CAPTURED`** or **`AuditEventType.PAYMENT_FAILED`**:
  ```json
  {
    "attempt_id": "att_9011ab44",
    "prior_status": "UNKNOWN",
    "resolved_status": "CAPTURED",
    "reconciliation_mode": "PROVIDER_QUERY",
    "provider_reference": "psp_tx_881923401"
  }
  ```

---

## 7. Cross-References
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md)
- [incident-runbook.md](file:///docs/operations/incident-runbook.md)

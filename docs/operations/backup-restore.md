# AgentPay Backup & Disaster Recovery Procedure (§45, §176)

## 1. Scope & Objective
This procedure defines backup, snapshotting, and point-in-time recovery for the AgentPay control plane:
- **Immutable Audit Ledger**: SHA-256 tamper-evident hash chain (§29, §45).
- **Tenant & Agent Registry**: Cryptographic credentials and spending policies (§15, §18).
- **Commerce & Inventory Catalog**: Real-time reservation states (§30, §37).
- **Payment & Order Records**: Payment attempts and saga compensation logs (§41, §44).

---

## 2. Backup Schedules & Formats

| Component | Target Location | Frequency | Retention | Encryption |
|---|---|---|---|---|
| **Audit Hash Ledger** | Append-only object store + WORM storage | Continuous stream / 15m snapshots | 7 years (regulatory) | AES-256-GCM / KMS |
| **PostgreSQL Database** | Georeplicated snapshot storage | WAL archiving + Hourly snapshot | 90 days | En-route + At-rest |
| **Redis Cache / State** | Redis RDB / AOF snapshots | Every 6 hours | 14 days | Encrypted volume |

---

## 3. Audit Ledger Chain Integrity Verification
Before taking or restoring any backup, the hash chain MUST be verified:

```python
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger

def verify_tenant_backup(ledger: InMemoryAuditLedger, tenant_id: str) -> bool:
    # Recomputes SHA-256(canonical_json(event) + previous_event_hash)
    is_valid = ledger.verify_chain(tenant_id)
    if not is_valid:
        raise ValueError(f"CRITICAL: Audit chain corruption detected for tenant {tenant_id}")
    return True
```

---

## 4. Disaster Recovery & Restoration Procedure

### Phase 1: Environment Freeze
1. Engage cluster-wide emergency pause:
   `POST /api/v1/emergency/pause` with reason `"Disaster recovery restore initiated"`.

### Phase 2: State Restoration
1. Restore database volume from the most recent certified snapshot.
2. Apply write-ahead log (WAL) replay to point-of-failure.
3. Validate schema migrations and tenant partition constraints (§49).

### Phase 3: Hash Chain & Payment Reconciliation
1. Run `verify_chain(tenant_id)` across all active tenants.
2. Execute `PaymentReconciler.reconcile(...)` against payment gateway provider APIs for all orders in status `PENDING` or `UNKNOWN` to synchronize transaction state (§43).

### Phase 4: Operational Unpause
1. Verify CI gate health checks.
2. Resume autonomous traffic via `POST /api/v1/emergency/resume`.

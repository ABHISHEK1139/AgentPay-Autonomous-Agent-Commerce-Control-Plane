# AgentPay Backup & Disaster Recovery Guide (§45, §176)

> **Document Status**: Production Standard  
> **Target Audience**: Site Reliability Engineers (SRE), Database Administrators (DBA), Security Auditors, Compliance Officers  
> **Governing Specifications**: `AGENTS.md` (§29, §41, §43, §45, §46, §176), ADR-0001, ADR-0002  
> **Last Verified**: Sprint 12 Release Hardening (2026-10-07)  

---

## 1. Scope & SLA Matrix

The AgentPay disaster recovery strategy guarantees data survivability, strict transaction non-repudiation, and fast service restoration across all primary components.

### 1.1 Recovery Objectives (RPO & RTO)

| System Component | Recovery Point Objective (RPO) | Recovery Time Objective (RTO) | Regulatory & Integrity Rationale |
|---|---|---|---|
| **Immutable Audit Ledger** | **RPO = 0** (Zero data loss) | **RTO ≤ 15 min** | Non-negotiable financial audit requirement (§29, §45). Continuous streaming replication to WORM object storage. |
| **Transactional Database (PostgreSQL)** | **RPO ≤ 5 min** | **RTO ≤ 15 min** | Protects order lifecycle, tenant policies, agent registrations, and spending counters (§15, §18). |
| **State Cache & Loop Budgets (Redis)** | **RPO ≤ 1 sec** | **RTO ≤ 5 min** | Fast session recovery and loop counter preservation (§64). Reconstructible from database if necessary. |
| **Control Plane Web Dashboard** | **RPO = 0** (Stateless) | **RTO ≤ 2 min** | Automated container spin-up and edge routing. |

---

## 2. Component Backup Schedules & Storage Architecture

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENTPAY PRODUCTION CLUSTER                     │
│                                                                        │
│  ┌───────────────────────┐  ┌────────────────────┐  ┌───────────────┐  │
│  │  PostgreSQL Primary   │  │   Redis Cluster    │  │ Audit Ledger  │  │
│  └──────────┬────────────┘  └─────────┬──────────┘  └───────┬───────┘  │
└─────────────┼─────────────────────────┼─────────────────────┼──────────┘
              │ Continuous WAL Stream   │ RDB Snapshots (6h)  │ Streaming
              │ Hourly Base Backups     │ AOF (every 1s)      │ Append-Only
              ▼                         ▼                     ▼
┌──────────────────────────┐  ┌────────────────────┐  ┌──────────────────┐
│ Cloud Encrypted Storage  │  │ Backup Volume      │  │ Cloud WORM S3/GCS│
│ (Geo-replicated, 90d)    │  │ (Encrypted, 14d)   │  │ (Object Lock, 7y)│
└──────────────────────────┘  └────────────────────┘  └──────────────────┘
```

### 2.1 Backup Specifications by Component

| Component | Target Storage Location | Frequency & Strategy | Retention Period | Encryption & Immutability |
|---|---|---|---|---|
| **Audit Ledger** | Geo-distributed WORM Object Storage (AWS S3 Object Lock / GCS Bucket Lock) | Continuous real-time event streaming + 15-minute sealed micro-batches | 7 years (Statutory financial compliance) | AES-256-GCM / Customer-Managed KMS Key; Compliance Mode Object Lock (strictly prevents deletion or alteration). |
| **PostgreSQL Database** | Geo-replicated encrypted block storage | Continuous Write-Ahead Log (WAL) archiving via pgBackRest + Hourly full snapshots | 90 days point-in-time recovery | En-route TLS 1.3 + At-rest AES-256; automated lifecycle transition to cold tier after 30 days. |
| **Redis Cache** | Encrypted backup object storage | Append-Only File (AOF) with `fsync everysec` + RDB snapshot every 6 hours | 14 days | Volume encryption (AES-256); ephemeral operational state. |

---

## 3. Cryptographic Hash Chain Verification (§45, §46)

The audit ledger employs a strict cryptographic hash chain to make unauthorized modifications, back-dating, or transaction deletions mathematically provable.

### 3.1 Cryptographic Hash Formula

Every audit event is cryptographically linked to its predecessor using SHA-256 over canonical JSON:

$$\text{EventHash}_0 = \text{SHA256}(\text{CanonicalJSON}(\text{Event}_0) + \text{GENESIS\_HASH})$$
$$\text{EventHash}_n = \text{SHA256}(\text{CanonicalJSON}(\text{Event}_n) + \text{EventHash}_{n-1})$$

Where:
- $\text{GENESIS\_HASH} = \text{"0"}\times 64$ (`0000000000000000000000000000000000000000000000000000000000000000`)
- `CanonicalJSON(data)` uses strict sorting and deterministic formatting:
  ```python
  json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
  ```
- **Preimage Incorporation**: The canonical preimage includes event metadata (`actor`, `event_type`, `timestamp`, `tenant_id`, `agent_id`, `action_id`), the event payload (`data`), and the predecessor hash (`previous_event_hash`).
- **Data Minimization (§40)**: `SensitiveFieldRedactor` strips card numbers, CVVs, passwords, API keys, and session tokens before canonical hashing.

### 3.2 Automated Chain Verification Logic

Within the codebase, `verify_chain(tenant_id)` validates the complete chain sequentially:

```python
def verify_chain(self, tenant_id: str) -> bool:
    with self._lock:
        events = self._tenant_events.get(tenant_id, [])
        if not events:
            return True
        for idx, event in enumerate(events):
            expected_prev = GENESIS_HASH if idx == 0 else events[idx - 1].event_hash
            if event.previous_event_hash != expected_prev:
                return False
            recomputed = AuditHasher.compute_event_hash(
                event.data,
                event.previous_event_hash,
                actor=event.actor,
                event_type=event.event_type.value,
                timestamp=event.timestamp.isoformat(),
                tenant_id=event.tenant_id,
                agent_id=event.agent_id,
                action_id=event.action_id,
            )
            if event.event_hash != recomputed:
                return False
        return True
```

### 3.3 Standalone Operational Verification Tool

Auditors and SREs can execute independent verification against exported ledger streams:

```python
#!/usr/bin/env python3
"""scripts/verify_audit_chain.py - Independent Audit Ledger Verification Tool."""
import json
import sys
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.domain.audit import GENESIS_HASH, AuditEvent, AuditEventType, AuditHasher

def verify_exported_stream(file_path: str) -> bool:
    print(f"[*] Loading audit ledger stream from: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    tenant_events: dict[str, list[dict]] = {}
    for r in records:
        tenant_events.setdefault(r["tenant_id"], []).append(r)

    total_verified = 0
    for tenant_id, events in tenant_events.items():
        print(f"[*] Verifying tenant: {tenant_id} ({len(events)} events)...")
        for idx, ev in enumerate(events):
            expected_prev = GENESIS_HASH if idx == 0 else events[idx - 1]["event_hash"]
            if ev["previous_event_hash"] != expected_prev:
                print(f"[!] FAILED: Broken linkage at event {ev['event_id']}")
                return False
            
            recomputed = AuditHasher.compute_event_hash(
                ev["data"],
                ev["previous_event_hash"],
                actor=ev["actor"],
                event_type=ev["event_type"],
                timestamp=ev["timestamp"],
                tenant_id=ev["tenant_id"],
                agent_id=ev.get("agent_id"),
                action_id=ev.get("action_id"),
            )
            if ev["event_hash"] != recomputed:
                print(f"[!] FAILED: Content tamper detected at event {ev['event_id']}")
                return False
            total_verified += 1

    print(f"[+] SUCCESS: All {total_verified} events verified across {len(tenant_events)} tenants.")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_audit_chain.py <path_to_audit_dump.jsonl>")
        sys.exit(1)
    success = verify_exported_stream(sys.argv[1])
    sys.exit(0 if success else 1)
```

---

## 4. Point-in-Time Recovery (PITR) Execution Procedure

When recovering from catastrophic database corruption or regional failure, follow this exact 5-phase execution runbook:

```text
PHASE 1: Freeze Environment (Kill Switch)
   ↓
PHASE 2: Volume & Snapshot Restoration (Postgres Base + WAL Replay)
   ↓
PHASE 3: Cryptographic Integrity Validation (verify_chain())
   ↓
PHASE 4: Payment Gateway & Saga Reconciliation (PaymentReconciler)
   ↓
PHASE 5: Operational Resumption (Health Checks + Unpause)
```

### Phase 1: Environment Freeze
1. Immediately engage the emergency kill switch across the target cluster:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/emergency/pause" \
     -H "X-Operator-ID: dr_operator_01" \
     -H "X-Tenant-ID: system" \
     -H "Content-Type: application/json" \
     -d '{"operator_id": "dr_operator_01", "reason": "PITR Disaster Recovery Initiated", "scope": "ALL"}'
   ```
2. Verify all mutation routes return HTTP 503 `EMERGENCY_PAUSE_ACTIVE`.

### Phase 2: Volume & Snapshot Restoration
1. Provision target database compute instance in the recovery zone.
2. Restore the latest full base backup snapshot prior to incident timestamp $T_{\text{target}}$.
3. Configure `recovery.signal` and `postgresql.conf` for WAL replay:
   ```ini
   restore_command = 'pgbackrest --stanza=agentpay archive-get %f "%p"'
   recovery_target_time = '2026-10-07 18:45:00 UTC'
   recovery_target_action = 'promote'
   ```
4. Start PostgreSQL service and monitor replay logs until standby promotion completes.

### Phase 3: Cryptographic Integrity Validation
1. Export restored audit ledger tables into JSONL format.
2. Execute the verification script:
   ```bash
   uv run python scripts/verify_audit_chain.py /tmp/restored_audit_export.jsonl
   ```
3. If verification fails, stop immediately. Compare against the external immutable WORM object storage stream to locate the corrupted transaction range.

### Phase 4: Payment Gateway & Saga Reconciliation (§43)
1. Initialize the `PaymentReconciler` service.
2. Query the payment provider (Razorpay / Sandbox PSP) for all orders in status `PENDING`, `AUTHORIZED`, or `UNKNOWN` created within the disaster window:
   ```python
   reconciler = PaymentReconciler(payment_provider=payment_provider)
   unreconciled_orders = order_repo.find_indeterminate_orders(since=disaster_window_start)
   for order in unreconciled_orders:
       status = reconciler.reconcile(tenant_id=order.tenant_id, payment_intent_id=order.payment_intent_id)
       print(f"Reconciled order {order.order_id}: Provider status = {status}")
   ```
3. **Execute Saga Compensations**:
   - For charges captured at provider without completed order: Issue refund or create completed order.
   - For orders marked completed but uncaptured at provider: Roll back order and release reserved inventory.

### Phase 5: Production Resumption
1. Execute the 5 verification quality gates:
   ```powershell
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy
   uv run pytest
   node --test tests/web.test.mjs
   ```
2. Disengage kill switch:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/emergency/resume" \
     -H "X-Operator-ID: dr_operator_01" \
     -H "X-Tenant-ID: system" \
     -H "Content-Type: application/json" \
     -d '{"operator_id": "dr_operator_01"}'
   ```
3. Announce operational restoration to stakeholders and record post-mortem.

---

## 5. Disaster Recovery Game-Day Drills

To guarantee operational readiness, the platform team conducts scheduled disaster simulations.

### 5.1 Drill Schedule & Scenarios

| Drill Cadence | Scenario Description | Success Criteria |
|---|---|---|
| **Monthly** | **Audit Hash Tamper Simulation**: Synthetic injection of corrupted bit into audit table. | Monitoring triggers Sev 1 alert; automated verification script isolates corrupted row; recovery from WORM replica completes in < 10 min. |
| **Quarterly** | **Payment Gateway Outage & Replay**: Simulated 30-minute gateway blackout during peak agent checkout. | Zero double charges; 100% of indeterminate transactions resolved via `PaymentReconciler`; saga compensations release 100% of held inventory. |
| **Bi-Annual** | **Full Region Failover (PITR)**: Complete loss of primary cloud region. | Standby cluster restored in secondary region with RPO < 5 min and RTO < 15 min; all 381 automated tests and web tests pass on restored instance. |

### 5.2 Game-Day Attestation Log

Drill results are formally reviewed by SecOps and signed off in `docs/security/standards-matrix.md` under NIST AI RMF and PCI DSS resilience requirements.

# Disaster Recovery & Regional Failover Runbook (§45, §49, §133, §176)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-009`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 5m, Failover Decision ≤ 15m, Target RTO ≤ 15m, Target RPO: Audit = 0, DB ≤ 5m)  
> **Service / Module**: `agentpay.infrastructure.disaster_recovery`, `agentpay.domain.audit`, `agentpay.application.services.reconciliation_service`  
> **Author / Owner**: Incident Commander & Lead Database Administrator (DBA)  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§45, §49, §133, §176), `docs/operations/backup-restore.md`  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay is a mission-critical autonomous commerce control plane. In the event of a catastrophic regional cloud outage, physical data center loss, or unrecoverable database corruption in the primary cloud region (e.g. AWS `ap-south-1` or GCP `asia-south1`), operations must fail over to the secondary disaster recovery (DR) region with zero audit loss and minimal operational disruption.

This runbook specifies:
- Recovery Point Objectives (RPO) and Recovery Time Objectives (RTO) across subsystems.
- Authority framework and criteria for declaring a regional disaster.
- Step-by-step cutover procedures including database replica promotion, cryptographic audit chain verification, and payment reconciliation.
- Global traffic redirection via GSLB and DNS routing.
- Orderly failback procedures once the primary region is fully restored.

---

## 2. Recovery Objectives & RPO/RTO Targets (§176)

AgentPay defines strict mathematical and operational recovery limits:

| Subsystem | Storage Mechanism | Target RPO | Target RTO | Verification Requirement |
|---|---|---|---|---|
| **Audit Ledger** | SHA-256 Chained WORM S3/GCS Object Lock | **0** (Zero loss) | **≤ 15 min** | Cryptographic hash verification via `scripts/verify_audit_chain.py` must return exit code 0. |
| **PostgreSQL Database** | Continuous WAL Streaming & Point-in-Time Recovery | **≤ 5 min** | **≤ 15 min** | Clean replay to consistent transaction boundary; foreign key integrity verified. |
| **Redis State & Idempotency** | AOF (Everysec) & RDB Snapshots | **≤ 1 sec** | **≤ 5 min** | Hydration verification; cold-start fallback to database records. |
| **API & Web Control Center** | Stateless Container Replicas | **0** | **≤ 2 min** | Health check `/api/v1/health` returns HTTP 200 OK. |

---

## 3. Disaster Declaration & Authority Framework

A regional disaster declaration authorizes the permanent or temporary failover of live autonomous financial traffic to secondary infrastructure.

### Authority
A regional failover may be declared ONLY by the **Incident Commander (IC)** upon unanimous consensus with the **Lead DBA** and **Platform SRE Lead**.

### Criteria for Declaration
1. **Primary Region Unavailability**: Cloud provider confirms widespread availability zone failure exceeding 30 minutes without estimated recovery.
2. **Catastrophic Data Loss**: Primary storage volume failure unrecoverable by local failover.
3. **Severe Cyber Attack / Physical Destruction**: Primary datacenter compromised or severed from network backbone.

---

## 4. Diagnostic Steps

### 4.1 Verify Primary Region Outage via PowerShell
```powershell
# Probe primary ingress
try {
    $res = Invoke-WebRequest -Uri "https://api-primary.agentpay.com/api/v1/health" -TimeoutSec 5
    Write-Host "Primary region responded: $($res.StatusCode)"
} catch {
    Write-Host "Primary region UNREACHABLE: $($_.Exception.Message)"
}
```

### 4.2 Inspect Secondary DR Site Readiness
```powershell
# Probe secondary DR ingress
$res = Invoke-RestMethod -Uri "https://api-dr.agentpay.internal/api/v1/health" -Method Get
Write-Host "Secondary cluster health: $($res.status)"
```

---

## 5. Step-by-Step Regional Cutover Protocol

### Step 1: Engage Emergency Pause on Secondary Region

Ensure the DR cluster does not begin executing autonomous transactions before data consistency and audit integrity are verified:

```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
$body = @{
    operator_id = "dr_lead_01"
    reason      = "DR cutover initialization - holding transactions during replica promotion"
    scope       = "ALL"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://api-dr.agentpay.internal/api/v1/emergency/pause" `
    -Method Post -Headers $headers -Body $body
```

### Step 2: Promote Secondary PostgreSQL Replica to Primary

Execute replica promotion on the secondary database cluster:

#### Linux / Container:
```bash
patroni_ctl failover agentpay-cluster --candidate dr-db-node-01 --force
```

#### Standalone Postgres Command:
```bash
pg_ctl promote -D /var/lib/postgresql/data
```

Verify the promoted database accepts write transactions and all tenant partitions are intact.

### Step 3: Cryptographic Audit Ledger Integrity Verification

Export restored audit events from the WORM storage bucket and run the verification CLI:

```powershell
uv run python scripts/verify_audit_chain.py C:\restore\audit_events_restored.jsonl
```

*Required Output*:
```text
[VERIFY] Checking audit events across all restored tenants...
[SUCCESS] All hashes verified. Chain integrity: 100%. Exit code: 0.
```

### Step 4: Reconcile In-Flight Payment States

Run `PaymentReconciler` across all tenants to identify and resolve any payment transactions that were in-flight at the moment of disaster:

```python
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.application.ports.payments_port import PaymentProvider, PaymentRepository
from agentpay.domain.payments import PaymentStatus

def reconcile_dr_payments(payment_provider: PaymentProvider, payment_repo: PaymentRepository, tenant_id: str):
    reconciler = PaymentReconciler(payment_provider, payment_repo)
    unknowns = [a for a in payment_repo.list_for_tenant(tenant_id) if a.status == PaymentStatus.UNKNOWN]
    print(f"Reconciling {len(unknowns)} in-flight attempts for tenant {tenant_id}...")
    for att in unknowns:
        reconciler.reconcile_unknown_payment(tenant_id, att.attempt_id)
```

### Step 5: Update Global DNS / GSLB Routing

Point the global routing layer (Cloudflare / AWS Route 53) to the DR cluster ingress IP:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z12345678 \
  --change-batch file://dns-failover-to-dr.json
```

### Step 6: Disengage Pause & Resume Autonomous Operations

Once health checks `/api/v1/health` pass across all DR instances:

```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
$body = @{ operator_id = "dr_lead_01" } | ConvertTo-Json

Invoke-RestMethod -Uri "https://api.agentpay.com/api/v1/emergency/resume" `
    -Method Post -Headers $headers -Body $body
```

---

## 6. Verification & Health Restoration

1. Verify that `GET https://api.agentpay.com/api/v1/health` returns HTTP 200 OK with `"emergency_paused": false`.
2. Verify that `PaymentRepository` contains zero attempts in `PaymentStatus.UNKNOWN`.
3. Submit a test action request from a canary agent and confirm complete end-to-end evaluation.

---

## 7. Failback Protocol (Return to Primary Region)

Following primary cloud region restoration:
1. **Establish Reverse Replication**: Configure Primary region database as replica receiving WAL stream from DR site.
2. **Sync Delta WALs**: Allow replication to catch up until replication lag is 0 bytes.
3. **Schedule Maintenance Window**: Coordinate a 10-minute maintenance window during low traffic.
4. **Orderly Switchback**:
   - Engage emergency pause on DR site.
   - Promote Primary database to master.
   - Repoint GSLB/DNS back to Primary region ingress IP.
   - Resume autonomous operations.

---

## 8. Audit Trail Accounting & Post-Mortem

Regional failover operations emit high-severity audit records:
- **Event Type**: `AuditEventType.SECURITY_EVENT` / `EMERGENCY_PAUSE`
- **Data Payload**:
```json
{
  "event": "REGIONAL_FAILOVER_EXECUTED",
  "source_region": "ap-south-1",
  "target_region": "ap-southeast-1",
  "declared_by": "ic_lead_01",
  "rto_achieved_minutes": 11.4,
  "rpo_achieved_seconds": 0,
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 5-Whys Post-Mortem Template
1. **Why was regional failover initiated?** Complete loss of AWS ap-south-1 network connectivity.
2. **Why was the DR site promoted?** Primary region was unrecoverable within 15-minute SLA.
3. **Why was audit ledger data fully preserved?** Continuous streaming to immutable WORM object storage ensured RPO = 0.
4. **Why did payments avoid double charges?** PaymentReconciler queried PSP APIs before completing in-flight transactions.
5. **How will recurrence be mitigated?** Evaluate active-active multi-region deployment architecture for Sprint 14.

---

## 9. Related References & Runbooks
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Emergency kill switch operation.
- [03-payment-timeout-reconciliation-runbook.md](file:///docs/runbooks/03-payment-timeout-reconciliation-runbook.md) — Payment reconciliation.
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md) — Audit chain recovery.
- [docs/operations/backup-restore.md](file:///docs/operations/backup-restore.md) — Backup and point-in-time recovery standards.

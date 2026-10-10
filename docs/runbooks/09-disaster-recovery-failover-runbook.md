# Disaster Recovery & Regional Failover Runbook (§45, §49, §133, §176)

> **Document Status**: Production Standard  
> **Target Audience**: Incident Commander (IC), Lead Database Administrator (DBA), SRE Team  
> **Governing Specifications**: `AGENTS.md` (§45, §49, §133, §176), `docs/operations/backup-restore.md`  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Recovery Objectives & RPO/RTO Targets (§176)

AgentPay defines strict Recovery Point Objectives (RPO) and Recovery Time Objectives (RTO) across system components:

| Subsystem | Storage Mechanism | Target RPO | Target RTO | Verification Requirement |
|---|---|---|---|---|
| **Audit Ledger** | SHA-256 Chained WORM S3/GCS Object Lock | **0** (Zero loss) | **≤ 15 min** | Cryptographic hash verification via `scripts/verify_audit_chain.py` must return exit code 0. |
| **PostgreSQL Database** | Continuous WAL Streaming & Point-in-Time Recovery | **≤ 5 min** | **≤ 15 min** | Clean replay to consistent transaction boundary; foreign key integrity verified. |
| **Redis State & Idempotency** | AOF (Everysec) & RDB Snapshots | **≤ 1 sec** | **≤ 5 min** | Hydration verification; cold-start fallback to database records. |
| **API & Web Control Center** | Stateless Container Replicas | **0** | **≤ 2 min** | Health check `/api/v1/health` returns HTTP 200 OK. |

---

## 2. Disaster Declaration & Invocation Authority

Regional failover may only be declared by the **Incident Commander** upon consultation with the Lead DBA and SRE Lead under the following conditions:
- Total primary cloud region failure (e.g. AWS `ap-south-1` unrecoverable outage).
- Irreparable storage corruption affecting primary database clusters.
- Malicious ransomware or physical infrastructure destruction.

---

## 3. Step-by-Step Regional Cutover Protocol

### Step 1: Engage Emergency Pause on Secondary Region

Ensure the disaster recovery (DR) cluster does not begin executing autonomous transactions before data consistency is verified:

```bash
curl -s -X POST "http://dr-cluster.agentpay.internal/api/v1/emergency/pause" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "dr_lead_01",
    "reason": "DR cutover initialization - holding transactions during replica promotion",
    "scope": "ALL"
  }'
```

### Step 2: Promote Secondary PostgreSQL Replica

Execute replica promotion to promote the standby database cluster to Primary:

```bash
# Example via Patroni / pgBackRest
patroni_ctl failover agentpay-cluster --candidate dr-db-node-01 --force
```

Verify database accepts write queries and all tenant partitions are intact.

### Step 3: Verify Audit Ledger Integrity

Export restored audit events and run the verification CLI:

```bash
uv run python scripts/verify_audit_chain.py /restore/audit_events.jsonl
```

*Required Output*:
```text
[VERIFY] Checking audit events across all restored tenants...
[SUCCESS] All hashes verified. Chain integrity: 100%. Exit code: 0.
```

### Step 4: Reconcile Open Payment States

Run `PaymentReconciler` across all tenants to identify and resolve any payment transactions that were in-flight at the moment of disaster:

```python
from agentpay.application.services.reconciliation_service import PaymentReconciler
# Inspect and reconcile all UNKNOWN attempts against PSP APIs
```

### Step 5: Update Global DNS / GSLB

Point the global routing layer (Cloudflare / AWS Route 53) to the DR cluster ingress IP:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z12345678 \
  --change-batch file://dns-failover-to-dr.json
```

### Step 6: Disengage Pause & Resume Operations

Once health checks `/api/v1/health` pass across all DR instances:

```bash
curl -s -X POST "http://api.agentpay.com/api/v1/emergency/resume" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{"operator_id": "dr_lead_01"}'
```

---

## 4. Failback Protocol (Return to Primary Region)

Following primary region restoration:
1. Establish reverse replication (Secondary -> Primary).
2. Sync delta WALs until replication lag is zero.
3. Schedule low-traffic maintenance window.
4. Engage emergency pause, promote primary, repoint DNS, and resume.

---

## 5. Cross-References
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)
- [03-payment-timeout-reconciliation-runbook.md](file:///docs/runbooks/03-payment-timeout-reconciliation-runbook.md)
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md)
- [backup-restore.md](file:///docs/operations/backup-restore.md)

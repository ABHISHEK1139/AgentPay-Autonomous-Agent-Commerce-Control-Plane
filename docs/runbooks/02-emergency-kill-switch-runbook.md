# Emergency Kill Switch Runbook (§21, §67, §176)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-002`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 2m, MTTC ≤ 2m, MTTR ≤ 1h)  
> **Service / Module**: `agentpay.api.app`, `agentpay.api.emergency`  
> **Author / Owner**: Incident Commander & Platform SRE Lead  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§21, §67, §133, §176), ADR-0001, ADR-0002  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay provides an immediate, cluster-wide emergency kill switch to instantly halt autonomous transactions when systemic anomalies occur. Engaging the kill switch prevents financial loss, double debits, and cascading failures across all connected tenants and autonomous agents.

This runbook specifies:
- The non-negotiable architectural invariants governing the kill switch.
- Trigger conditions, severity classification, and detection alarms.
- Step-by-step diagnostic and execution procedures using cURL, PowerShell, and Python.
- Fail-closed safeguards ensuring all mutating paths are blocked while diagnostic reads remain open.
- The authoritative 4-step resumption protocol (§176) required before re-enabling autonomous payments.

---

## 2. Kill Switch Architecture & Invariants

1. **In-Process Cluster-Wide Intercept**: Engaging the kill switch flips the global flag `_emergency_paused = True`. Every mutating action request and protocol checkout path is immediately intercepted at the API and orchestrator boundary.
2. **Fail-Closed on Mutating Paths**: All endpoints that can move money, reserve inventory, or mutate approvals fail closed immediately with HTTP 503 `EMERGENCY_PAUSE_ACTIVE`.
3. **Safe Read-Only Operations Remain Available**: The kill switch does NOT terminate the server process. Diagnostic read routes (`/api/v1/health`, `/openapi.json`, `/agent/catalog`, `/api/v1/emergency/status`) remain fully functional to allow operators to inspect system telemetry during an ongoing outage.
4. **Operator Attribution & Role Gating**: The emergency control plane requires operator authentication (`UserRole.AGENT_OPERATOR` or `UserRole.ADMIN`). Unauthorized callers receive HTTP 403 `UNAUTHORIZED_OPERATOR`.

---

## 3. Trigger Conditions & Detection Alarms

| Trigger Condition | Description | Severity | Target MTTC |
|---|---|---|---|
| **Systemic Double-Debit Anomaly** | Payment gateway reports repeated charge retries or idempotency store failure across multiple merchants. | Sev 1 | ≤ 2 minutes |
| **Audit Ledger Hash Corruption** | `AuditLedger.verify_chain()` fails or `AuditChainIntegrityError` is raised in telemetry. | Sev 1 | ≤ 2 minutes |
| **Multi-Tenant Data Leakage** | BOLA/BFLA exploit or cross-tenant access violation detected in production traffic. | Sev 1 | ≤ 5 minutes |
| **Price Feed Poisoning** | Coordinated catalog feed attack corrupting inventory and price records across multiple vendors. | Sev 2 | ≤ 10 minutes |
| **Upstream Gateway Outage** | PSP returns sustained 5xx errors or socket hangs across all checkout attempts. | Sev 2 | ≤ 5 minutes |

---

## 4. Diagnostic Steps

### 4.1 Inspect Kill Switch Status via cURL
```bash
curl -s -X GET "http://localhost:8000/api/v1/emergency/status" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json"
```

### 4.2 Inspect Kill Switch Status via PowerShell
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/emergency/status" -Method Get -Headers $headers
```

*Expected Status Response (Normal Operations)*:
```json
{
  "paused": false,
  "emergency_paused": false,
  "metadata": {}
}
```

### 4.3 Inspect System Health Endpoint
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -Method Get
```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Engage Cluster-Wide Emergency Pause

#### Via cURL:
```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/pause" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "ic_lead_01",
    "reason": "Active audit hash chain tamper alarm detected in production cluster",
    "scope": "ALL_AUTONOMOUS_PAYMENTS"
  }'
```

#### Via Windows PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
$body = @{
    operator_id = "ic_lead_01"
    reason      = "Active audit hash chain tamper alarm detected in production cluster"
    scope       = "ALL_AUTONOMOUS_PAYMENTS"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/emergency/pause" `
    -Method Post -Headers $headers -Body $body
```

*Expected HTTP 200 Response*:
```json
{
  "status": "PAUSED",
  "paused": true,
  "emergency_paused": true,
  "metadata": {
    "paused_by": "ic_lead_01",
    "reason": "Active audit hash chain tamper alarm detected in production cluster",
    "scope": "ALL_AUTONOMOUS_PAYMENTS",
    "paused_at": "2026-10-10T12:00:00Z"
  }
}
```

### Step 2: Validate Kill Switch Intercept

Verify that mutating endpoints return HTTP 503 `EMERGENCY_PAUSE_ACTIVE`:

#### PowerShell Probe:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:AGENT_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
$body = @{
    action_id = "probe_01"
    domain    = "commerce"
    action    = "purchase"
} | ConvertTo-Json

try {
    Invoke-RestMethod -Uri "http://localhost:8000/api/v1/actions" -Method Post -Headers $headers -Body $body
    Write-Error "FAIL: Mutating action succeeded during emergency pause!"
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Host "Confirmed kill switch active: HTTP $statusCode (expected 503)."
}
```

*Expected HTTP 503 Error Envelope*:
```json
{
  "error": {
    "code": "EMERGENCY_PAUSE_ACTIVE",
    "message": "Autonomous payments are paused by operator emergency intervention",
    "request_id": "req_11a8b9c20144",
    "trace_id": "trace_8810fc029833aa12",
    "details": {
      "paused_by": "ic_lead_01",
      "reason": "Active audit hash chain tamper alarm detected in production cluster"
    }
  }
}
```

Verify that read routes remain operational:
```powershell
$health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -Method Get
Write-Host "Health status: $($health.status), Emergency Paused: $($health.emergency_paused)"
# Output: Health status: healthy, Emergency Paused: True
```

### Step 3: Four-Step Resumption Protocol (§176)

Before resuming normal autonomous commerce operations, the Incident Commander must formally certify all four release criteria:

1. **Criterion 1: Root Cause Neutralized**  
   The vulnerability, rogue agent, or data corruption that triggered the pause has been identified, patched, or quarantined.
2. **Criterion 2: Audit Ledger Integrity Verified**  
   Run the audit chain verifier across all active tenant ledgers:
   ```powershell
   uv run python scripts/verify_audit_chain.py /var/log/agentpay/audit_events.jsonl
   ```
   Must return exit code 0 (`SUCCESS: All hashes verified`).
3. **Criterion 3: Payment State Reconciled**  
   Confirm zero payment attempts remaining in `PaymentStatus.UNKNOWN`:
   ```powershell
   uv run python -c "from agentpay.application.services.reconciliation_service import PaymentReconciler; print('Reconciliation check clean')"
   ```
4. **Criterion 4: Test Suite & Gate Validation Clean**  
   Execute full gate verification:
   ```powershell
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy
   uv run pytest
   ```

### Step 4: Disengage Pause & Resume Normal Operations

#### Via cURL:
```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/resume" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "ic_lead_01"
  }'
```

#### Via Windows PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
$body = @{ operator_id = "ic_lead_01" } | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/emergency/resume" `
    -Method Post -Headers $headers -Body $body
```

*Expected HTTP 200 Response*:
```json
{
  "status": "ACTIVE",
  "paused": false,
  "emergency_paused": false,
  "metadata": {
    "resumed_by": "ic_lead_01",
    "resumed_at": "2026-10-10T12:45:00Z"
  }
}
```

---

## 6. Verification & Health Checks

1. Verify that `GET /api/v1/health` returns `"emergency_paused": false`.
2. Submit a non-financial test action request and verify it evaluates through the policy engine normally.
3. Confirm across protocol endpoints (`/acp/*`, `/ap2/*`, `/uap/*`, `/x402/*`) that HTTP 503 responses have ceased.

---

## 7. Audit Trail Accounting & Post-Mortem

Both pause engagement and resumption emit immutable audit ledger records:
- **Event Type**: `AuditEventType.EMERGENCY_PAUSE`
- **Data Payload**:
```json
{
  "action": "ENGAGE_EMERGENCY_PAUSE",
  "operator_id": "ic_lead_01",
  "reason": "Active audit hash chain tamper alarm detected in production cluster",
  "scope": "ALL_AUTONOMOUS_PAYMENTS",
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 5-Whys Post-Mortem Template
1. **Why was the emergency kill switch engaged?** Tamper alarm or payment anomaly detected.
2. **Why did the anomaly affect the cluster?** Inconsistent upstream response or concurrent database contention.
3. **Why did the in-process boundary intercept it?** Pre-flight execution guard caught the state before financial capture.
4. **Why did resolution require X minutes?** Time required to run forensic audit-chain analysis and reconcile payment attempts.
5. **How will recurrence be prevented?** Automated ledger snapshotting, tightened PSP timeout thresholds, and improved health probes.

---

## 8. Related References & Runbooks
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md) — Targeted per-agent containment.
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md) — Cryptographic ledger recovery.
- [09-disaster-recovery-failover-runbook.md](file:///docs/runbooks/09-disaster-recovery-failover-runbook.md) — Secondary site cutover.

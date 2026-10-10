# Emergency Kill Switch Runbook (§21, §67, §176)

> **Document Status**: Production Standard  
> **Target Audience**: Incident Commander (IC), SecOps Lead, Site Reliability Engineers (SRE), Platform On-Call  
> **Governing Specifications**: `AGENTS.md` (§21, §67, §133, §176), ADR-0001, ADR-0002  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Kill Switch Architecture & Invariants

AgentPay provides an immediate, cluster-wide emergency kill switch to instantly halt autonomous transactions when systemic anomalies occur.

### 1.1 Non-Negotiable Architectural Invariants
1. **In-Process Cluster-Wide Intercept**: Engaging the kill switch flips `_emergency_paused = True`. Every mutating action request and protocol checkout path is immediately intercepted at the API and orchestrator boundary.
2. **Fail-Closed on Mutating Paths**: All endpoints that can move money or reserve inventory fail closed immediately with HTTP 503 `EMERGENCY_PAUSE_ACTIVE`.
3. **Safe Read-Only Operations Remain Available**: The kill switch does NOT terminate the server process. Diagnostic read routes (`/api/v1/health`, `/openapi.json`, `/agent/catalog`, `/api/v1/emergency/status`) remain fully functional to allow operators to inspect system telemetry during an ongoing outage.
4. **Operator Attribution & Role Gating**: The kill switch endpoints require an operator credential (`UserRole.AGENT_OPERATOR` or `UserRole.ADMIN`). Unauthorized or tenant-scoped standard callers receive HTTP 403 `UNAUTHORIZED_OPERATOR`.

---

## 2. Trigger Scenarios & Severity Classification

| Trigger Condition | Description | Severity | Target MTTC |
|---|---|---|---|
| **Systemic Double-Debit Anomaly** | Payment gateway reports repeated charge retries or idempotency store failure across multiple merchants. | Sev 1 | ≤ 2 minutes |
| **Audit Ledger Hash Corruption** | `AuditLedger.verify_chain()` fails or `AuditChainIntegrityError` is raised in telemetry. | Sev 1 | ≤ 3 minutes |
| **Multi-Tenant Data Leakage** | BOLA/BFLA exploit or cross-tenant access violation detected in production traffic. | Sev 1 | ≤ 5 minutes |
| **Price Feed Poisoning** | Coordinated catalog feed attack corrupting inventory and price records across multiple vendors. | Sev 2 | ≤ 10 minutes |

---

## 3. Operator Invocation Endpoints & Commands

### 3.1 Step 1: Engage Emergency Pause

Invoke the emergency pause endpoint using cURL or an operator workstation script:

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

### 3.2 Step 2: Validate Kill Switch Intercept

Verify that mutating endpoints return HTTP 503 `EMERGENCY_PAUSE_ACTIVE`:

```bash
# Test against core action submission
curl -s -X POST "http://localhost:8000/api/v1/actions" \
  -H "Authorization: Bearer <agent_token>" \
  -H "X-Tenant-ID: tenant_01" \
  -H "Content-Type: application/json" \
  -d '{"action_id": "probe_01", ...}'
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
```bash
curl -s -X GET "http://localhost:8000/api/v1/health"
# {"status": "healthy", "emergency_paused": true, "version": "1.0.0-mvp"}
```

---

## 4. Four-Step Resumption Protocol (§176)

Before resuming normal autonomous commerce operations, the Incident Commander must formally certify all four release criteria:

1. **Criterion 1: Root Cause Neutralized**  
   The vulnerability, rogue agent, or data corruption that triggered the pause has been identified, patched, or quarantined.
2. **Criterion 2: Audit Ledger Integrity Verified**  
   Run the audit chain verifier across all active tenant ledgers:
   ```bash
   uv run python scripts/verify_audit_chain.py /var/log/agentpay/audit_events.jsonl
   ```
   Must return exit code 0 (`SUCCESS: All hashes verified`).
3. **Criterion 3: Payment State Reconciled**  
   Confirm zero payment attempts remaining in `PaymentStatus.UNKNOWN`:
   ```bash
   uv run python -c "from agentpay.application.services.reconciliation_service import ...; ..."
   ```
4. **Criterion 4: Test Suite & Gate Validation Clean**  
   Execute full gate verification:
   ```powershell
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy
   uv run pytest
   ```

### Step 3: Disengage Pause & Resume Normal Operations

```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/resume" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "ic_lead_01"
  }'
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

## 5. Audit Trail Accounting & Post-Mortem

Both pause engagement and resumption emit immutable audit ledger records:
- **Event Type**: `AuditEventType.EMERGENCY_PAUSE`
- **Data Payload**: Includes operator attribution, timestamp, reason, and transition direction.

---

## 6. Cross-References
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md)
- [04-audit-chain-tamper-recovery-runbook.md](file:///docs/runbooks/04-audit-chain-tamper-recovery-runbook.md)
- [09-disaster-recovery-failover-runbook.md](file:///docs/runbooks/09-disaster-recovery-failover-runbook.md)

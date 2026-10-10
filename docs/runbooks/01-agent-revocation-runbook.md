# Agent Revocation & Offboarding Runbook (§14, §15, §16, §21, §133)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-001`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 5m, MTTC ≤ 10m, MTTR ≤ 2h) / Sev 2 (MTTA ≤ 15m, MTTC ≤ 30m, MTTR ≤ 4h)  
> **Service / Module**: `agentpay.domain.identity`, `agentpay.application.services.identity_service`  
> **Author / Owner**: SecOps Lead & Identity Engineering Team  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§14, §15, §16, §21, §133), ADR-0001, ADR-0002, RFC 9700  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

Autonomous AI agents in AgentPay execute financial actions, tool invocations, and catalog evaluations on behalf of human principals and tenant organizations. Because agents hold delegated financial authority within preconfigured policy limits, any credential compromise, behavioral anomaly, prompt injection exploitation, or privilege escalation requires immediate containment or terminal revocation.

This runbook specifies:
- Diagnostic steps to detect and confirm agent compromise or runaway loops.
- The decision matrix between temporary containment (`QUARANTINED` / `SUSPENDED`) and terminal decommission (`REVOKED`).
- Operational procedures across API, PowerShell, and Python administrative channels.
- Invalidation protocols for active tokens, pending human approvals, and held inventory.
- Verification checks ensuring fail-closed security and audit chain non-repudiation.

---

## 2. Incident Overview & SLA Matrix

### 2.1 Trigger Conditions & Detection Alarms
- **Compromised Credentials**: Leakage of agent API keys (`apk_*`), Bearer tokens, or signing secrets in external logs, public code repositories, or client crash telemetry.
- **Privilege Escalation Alarm**: Telemetry detects `PrivilegeEscalationAttemptError` (e.g. an agent attempting administrative tool execution like `set_spending_limit` or mutating authorization policies).
- **Runaway Execution Loop**: Alarm triggered by `LoopBudgetExceededError` or `AgentRunawayLoopError` (exceeding 5 tool calls or recursion depth > 3).
- **Spend / Price Drift Anomaly**: Agent generating high-frequency transaction requests with recurring price discrepancies (`ConflictType.PRICE_MISMATCH`) or policy limit violations.
- **Scheduled Offboarding**: Business sunsetting of an autonomous workflow or decommissioning of a service principal.

### 2.2 Severity & Response SLAs

| Severity Level | Trigger Criteria | MTTA (Acknowledge) | MTTC (Containment) | MTTR (Resolution) |
|---|---|---|---|---|
| **Sev 1 (Critical)** | Active spending anomaly, privilege escalation, or public credential leak with monetary loss risk. | ≤ 5 minutes | ≤ 10 minutes | ≤ 2 hours |
| **Sev 2 (High)** | Leak of scoped API key without observed fraudulent transactions; loop budget threshold tripped. | ≤ 15 minutes | ≤ 30 minutes | ≤ 4 hours |
| **Sev 3 (Standard)** | Normal service principal decommissioning or scheduled policy offboarding. | ≤ 1 business day | N/A | ≤ 2 business days |

---

## 3. Diagnostic & Triage Procedures

When an alert fires or anomalous agent behavior is reported, perform immediate triage to identify the agent identity, credential ID, tenant partition, and recent activity.

### 3.1 Log Search & Telemetry Analysis

#### Windows PowerShell:
```powershell
# Search API access logs for anomalous agent activity and trace IDs
Get-Content -Path "C:\var\log\agentpay\api_access.log" -Tail 5000 | 
    Select-String -Pattern "agent_id=ag_" | 
    Select-String -Pattern "403|500|PrivilegeEscalation|LoopBudget"
```

#### Bash / Ripgrep:
```bash
# Locate recent errors associated with a specific agent ID
rg "ag_checkout_bot" /var/log/agentpay/api_access.log | rg -E "403|429|500"
```

### 3.2 Diagnostic State Inspection via API

#### Using cURL:
```bash
curl -s -X GET "http://localhost:8000/api/v1/agents/{agent_id}" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: <tenant_id>" \
  -H "Content-Type: application/json"
```

#### Using PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agents/ag_checkout_bot" -Method Get -Headers $headers
```

### 3.3 Diagnostic State Inspection via Python CLI:
```python
from agentpay.adapters.in_memory.identity_repository import InMemoryAgentRepository

agent_repo = InMemoryAgentRepository()
agent = agent_repo.get("tenant_01", "ag_checkout_bot")
if agent:
    print(f"Agent ID: {agent.agent_id}, Status: {agent.status.value}, Capabilities: {agent.capabilities}")
```

---

## 4. Immediate Quarantine vs. Permanent Revocation Decision Tree

AgentPay enforces a strict finite state machine for agent status in `apps/api/src/agentpay/domain/identity.py`:
- `ACTIVE`: Normal autonomous operation permitted.
- `SUSPENDED`: Temporarily halted; can be resumed after operator review.
- `QUARANTINED`: Isolated under active security investigation; cannot transition directly to `ACTIVE` (must transition to `SUSPENDED` first for triage).
- `REVOKED`: **Strictly terminal state.** An agent in `REVOKED` state can NEVER be re-activated or transitioned to any other state (`AgentRevokedError`).

```
                    ┌──────────────┐
                    │    ACTIVE    │
                    └──────┬───────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
      ┌────────────┐ ┌───────────┐ ┌───────────┐
      │ SUSPENDED  │ │QUARANTINED│ │  REVOKED  │ (Terminal)
      └─────┬──────┘ └─────┬─────┘ └───────────┘
            │              │
            │◄─────────────┘ (Investigation Clearance)
            ▼
      ┌────────────┐
      │   ACTIVE   │ (Restoration)
      └────────────┘
```

**Decision Rules**:
1. **QUARANTINE**: Use when investigating suspected prompt injection, unverified telemetry spikes, or behavioral drift where false positives are possible.
2. **SUSPEND**: Use for administrative pauses, policy tuning, or pending merchant feed updates.
3. **REVOKE**: Use for confirmed credential leaks, deliberate abuse, unauthorized financial attempts, or permanent decommissioning.

---

## 5. Mitigation & Recovery Playbook

### Step 1: Immediate API Revocation / Quarantine

#### Terminal Revocation via cURL:
```bash
curl -s -X POST "http://localhost:8000/api/v1/agents/{agent_id}/revoke" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: <tenant_id>" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "secops_lead_01",
    "reason": "Compromised API key detected in public telemetry",
    "scope": "ALL"
  }'
```

#### Terminal Revocation via Windows PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
$body = @{
    operator_id = "secops_lead_01"
    reason      = "Compromised API key detected in public telemetry"
    scope       = "ALL"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agents/ag_checkout_bot/revoke" `
    -Method Post -Headers $headers -Body $body
```

### Step 2: Direct Domain Python Execution

When running direct maintenance in an administrative shell:

```python
from agentpay.domain.identity import AgentStatus
from agentpay.application.ports.identity_port import AgentRepository, CredentialRepository

def revoke_agent_emergency(
    agent_repo: AgentRepository,
    cred_repo: CredentialRepository,
    tenant_id: str,
    agent_id: str,
    operator_id: str,
    reason: str,
) -> None:
    # 1. Fetch current agent
    agent = agent_repo.get(tenant_id, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found in tenant {tenant_id}")
    
    # 2. Terminal state transition (fails if already REVOKED)
    revoked_agent = agent.transition_to(AgentStatus.REVOKED)
    agent_repo.save(tenant_id, revoked_agent)

    # 3. Invalidate all active credentials bound to this agent principal
    active_creds = cred_repo.list_by_principal(tenant_id, agent_id)
    for cred in active_creds:
        revoked_cred = cred.revoke(reason=f"Agent revocation by {operator_id}: {reason}")
        cred_repo.save(tenant_id, revoked_cred)
    
    print(f"Agent {agent_id} successfully revoked. Credentials invalidated: {len(active_creds)}")
```

### Step 3: Invalidation of Active Approvals & Held Reservations

Revoking an agent must cancel all in-flight authorizations:
1. **Purge Pending Human Approvals**: Query `ApprovalRepository` for pending approvals (`ApprovalStatus.PENDING`) associated with actions initiated by the revoked `agent_id`. Transition each to `REJECTED` or `CANCELLED` with reason `"Agent revoked prior to approval consumption"`.
2. **Release Held Inventory**: If an action request had reserved inventory stock but has not yet completed payment capture, trigger inventory release via `InventoryRepository.release(tenant_id, variant_id, qty)`.
3. **Purge Idempotency Store**: Drop pending reservation locks associated with the agent to prevent dangling transaction records.

---

## 6. Blast Radius & Fail-Closed Safeguards

- **Strict Tenant Partitioning (§49, §50)**: Revocation operations require explicit `tenant_id`. Revoking `ag_01` in `tenant_01` has zero operational impact on `tenant_02`.
- **Subsequent Request Rejection**:
  - Invocations presenting the revoked agent's token receive HTTP 401 `UNAUTHENTICATED` ("Credential has been revoked").
  - Actions referencing the revoked agent ID receive HTTP 403 `AGENT_REVOKED`:

```json
{
  "error": {
    "code": "AGENT_REVOKED",
    "message": "Agent 'ag_checkout_bot' is revoked and cannot perform actions",
    "request_id": "req_8f12a9c301e4",
    "trace_id": "trace_9901ef4412aa89bb",
    "details": {
      "agent_id": "ag_checkout_bot",
      "status": "REVOKED"
    }
  }
}
```

---

## 7. Verification & Health Checks

After executing revocation, confirm the following checks pass:

### 7.1 Verify Action Rejection via PowerShell
```powershell
$headers = @{
    "Authorization" = "Bearer $env:REVOKED_AGENT_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
    "Content-Type"  = "application/json"
}
$body = @{
    action_id = "test_probe_action"
    domain    = "commerce"
    action    = "purchase"
} | ConvertTo-Json

try {
    Invoke-RestMethod -Uri "http://localhost:8000/api/v1/actions" -Method Post -Headers $headers -Body $body
    Write-Error "CRITICAL: Revoked agent was allowed to submit an action!"
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Host "Verification PASSED: Received HTTP $statusCode (expected 401 or 403)."
}
```

### 7.2 Verify Tenant Health
Confirm that peer agents in the same tenant remain in `ACTIVE` status and continue processing valid purchases without degradation.

---

## 8. Post-Incident Actions & Audit Trail Requirements

### 8.1 Audit Trail Accounting
Every agent status transition emits an immutable audit event in the SHA-256 hash ledger:
- **Event Type**: `AuditEventType.AGENT_REVOKED` (or `SECURITY_EVENT`).
- **Payload Data**:
```json
{
  "agent_id": "ag_checkout_bot",
  "prior_status": "ACTIVE",
  "target_status": "REVOKED",
  "operator_id": "secops_lead_01",
  "revocation_reason": "Compromised API key detected in public telemetry",
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 8.2 5-Whys Post-Mortem Template
1. **Why was the agent revoked?** Credentials were exposed or loop budget was exhausted.
2. **Why were credentials exposed / loop budget tripped?** Insecure client deployment or unexpected recursive tool call chain.
3. **Why did the guardrail not prevent the initial loop?** Tool recursion limits caught the runaway at depth 3, successfully containing monetary loss.
4. **Why was the agent granted excessive tool authority?** Review permission scopes assigned to the service principal.
5. **How will recurrence be prevented?** Rotate client deployment keys, tighten rate limit buckets, and add prompt injection classifiers.

---

## 9. Related References & Runbooks
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Cluster-wide emergency pause procedures.
- [08-key-rotation-and-credential-revocation-runbook.md](file:///docs/runbooks/08-key-rotation-and-credential-revocation-runbook.md) — Credential rotation and invalidation.
- [docs/operations/incident-runbook.md](file:///docs/operations/incident-runbook.md) — General incident command framework.

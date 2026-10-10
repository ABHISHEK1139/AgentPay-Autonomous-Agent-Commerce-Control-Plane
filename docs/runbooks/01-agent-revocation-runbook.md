# Agent Revocation & Offboarding Runbook (§14, §15, §16, §21, §133)

> **Document Status**: Production Standard  
> **Target Audience**: SecOps Lead, Incident Commander (IC), Platform On-Call, Identity Administrators  
> **Governing Specifications**: `AGENTS.md` (§14, §15, §16, §21, §133), ADR-0001, ADR-0002, RFC 9700  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Incident Overview & SLA Matrix

Autonomous AI agents execute financial actions and tool invocations on behalf of human principals and tenant organizations. When an agent is compromised, behaves abnormally, attempts privilege escalation, or is decommissioned, operators must execute rapid containment or terminal revocation.

### 1.1 Trigger Conditions & Detection Alarms
- **Compromised Credentials**: Leakage of agent API keys, Bearer tokens, or signing secrets in external logs, public repositories, or client crash telemetry.
- **Privilege Escalation Alarm**: Detection of `PrivilegeEscalationAttemptError` (e.g. agent invoking administrative tools such as `set_spending_limit` or altering its own permission scopes).
- **Abusive Execution Loop**: Triggering of `LoopBudgetExceededError` or `AgentRunawayLoopError` (exceeding 5 tool calls or recursion depth > 3).
- **Anomalous Spend / Price Drift**: Agent generating transactions with repeated price discrepancies (`ConflictType.PRICE_MISMATCH`) or exceeding policy caps.
- **Scheduled Offboarding**: Formal decommissioning of service principal or business sunset of an autonomous workflow.

### 1.2 Severity & SLA Response Matrix

| Severity Level | Trigger Criteria | MTTA (Acknowledge) | MTTC (Containment) | MTTR (Resolution) |
|---|---|---|---|---|
| **Sev 1 (Critical)** | Active spending anomaly, privilege escalation, or public credential leak with monetary loss risk. | ≤ 5 minutes | ≤ 10 minutes | ≤ 2 hours |
| **Sev 2 (High)** | Leak of scoped API key without observed fraudulent transactions; loop budget threshold tripped. | ≤ 15 minutes | ≤ 30 minutes | ≤ 4 hours |
| **Sev 3 (Standard)** | Normal service principal decommissioning or scheduled policy offboarding. | ≤ 1 business day | N/A | ≤ 2 business days |

---

## 2. Containment Decision Tree: Quarantine vs. Terminal Revocation

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

**Decision Rule**:
- Use **QUARANTINED** when investigating an active alert, suspected prompt injection, or telemetry anomaly that may be a false positive.
- Use **REVOKED** when credentials have been publicly exposed, intentional abuse is verified, or the agent is permanently decommissioned.

---

## 3. Step-by-Step Execution Protocol

### Step 1: Immediate API Revocation / Quarantine

Execute HTTP POST to update agent lifecycle status. Requires an operator or administrator credential bound to the tenant:

```bash
# Terminal Revocation
curl -s -X POST "http://localhost:8000/api/v1/agents/{agent_id}/revoke" \
  -H "Authorization: Bearer <admin_token>" \
  -H "X-Tenant-ID: <tenant_id>" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "secops_lead_01",
    "reason": "Compromised API key detected in public telemetry",
    "scope": "ALL"
  }'
```

### Step 2: In-Memory / Domain Python Script Execution

When operating directly via administrative tooling or maintenance scripts:

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
```

---

## 4. Invalidation of Active Approvals & Held Reservations

Revoking an agent must cancel all in-flight authorizations:
1. **Purge Pending Human Approvals**: Query `ApprovalRepository` for pending approvals (`ApprovalStatus.PENDING`) associated with actions initiated by the revoked `agent_id`. Transition each to `REJECTED` or `CANCELLED` with reason `"Agent revoked prior to approval consumption"`.
2. **Release Held Inventory**: If an action request had reserved inventory stock but has not yet completed payment capture, trigger inventory release via `InventoryRepository.release(tenant_id, variant_id, qty)`.
3. **Purge Idempotency Store**: Drop pending reservation locks associated with the agent to prevent dangling transaction records.

---

## 5. Fail-Closed Safeguards & Blast Radius Limits

- **Isolated Tenant Scope**: Revocation is strictly partitioned by `tenant_id`. Executing revocation for `agent_01` in `tenant_01` has zero impact on `tenant_02`.
- **Subsequent Request Behavior**:
  - Invocations presenting the revoked agent's token receive HTTP 401 `UNAUTHENTICATED` ("Credential has been revoked").
  - Actions referencing the revoked agent ID receive HTTP 403 `AGENT_REVOKED` ("Agent agent_01 is revoked and cannot initiate actions").

Standard RFC error response:
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

## 6. Audit Ledger Accounting & Post-Mortem

Every status transition generates a tamper-evident audit record in the SHA-256 hash ledger:
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

### Post-Mortem Action Items
1. Verify audit chain integrity using `scripts/verify_audit_chain.py`.
2. Inspect payment ledger for unauthorized transactions attempted in the 60 minutes prior to revocation.
3. If fraudulent charges were captured, execute `PaymentReconciler` saga refund protocol (`03-payment-timeout-reconciliation-runbook.md`).

---

## 7. Cross-References
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)
- [08-key-rotation-and-credential-revocation-runbook.md](file:///docs/runbooks/08-key-rotation-and-credential-revocation-runbook.md)
- [incident-runbook.md](file:///docs/operations/incident-runbook.md)

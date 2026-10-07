# AgentPay Incident Response Runbook (§21, §67, §176)

## 1. Purpose & Authority
This runbook establishes mandatory incident handling procedures for operational, security, and financial anomalies in the AgentPay autonomous commerce control plane.

## 2. Emergency Kill Switch Protocols (§21, §67)

### 2.1 Kill Switch Actions
When an anomaly, infinite loop, price manipulation, or unauthorized prompt injection is detected, an authorized security operator MUST invoke the emergency controls:

| Action | API Endpoint | Scope | Fail-Closed Effect |
|---|---|---|---|
| `PAUSE_ALL_AUTONOMOUS_PAYMENTS` | `POST /api/v1/emergency/pause` | Cluster-wide | Immediately halts all autonomous agent purchases (HTTP 503 `EMERGENCY_PAUSE_ACTIVE`). Read-only queries remain available. |
| `PAUSE_TENANT` | `POST /api/v1/emergency/pause` with `tenant_id` | Tenant isolation | Halts all transactions for the affected tenant organization. |
| `PAUSE_AGENT` | `POST /api/v1/emergency/pause` with `agent_id` | Agent quarantine | Sets agent status to `QUARANTINED`. Rejects all new action requests with `AgentQuarantinedError`. |
| `REVOKE_AGENT` | `POST /api/v1/emergency/revoke` with `agent_id` | Permanent revocation | Sets agent status to `REVOKED`. Cryptographically revokes capability delegation tokens. |

### 2.2 Operator Attribution & Audit (§67)
Every emergency pause and resume invocation MUST record:
- **`who`**: Operator principal ID (e.g. `sec_operator_01`)
- **`when`**: UTC timestamp (`ISO 8601`)
- **`why`**: Justification / incident reference
- **`scope`**: Affected tenant, agent, or cluster
- **`audit hash`**: SHA-256 hash chained into tenant ledger

---

## 3. Incident Classification & Triage Matrix

### Severity 1 (Critical) — Immediate Financial / Security Risk
- **Triggers**:
  - Agent attempts to widen its own spending limit (`PrivilegeEscalationAttemptError`).
  - Evidence tampering or price manipulation detected (`PRICE_MISMATCH`).
  - Payment gateway timeout resulting in status `UNKNOWN` requiring reconciliation.
  - Audit hash chain integrity failure (`AuditChainIntegrityError`).
- **Response**:
  1. Trigger `PAUSE_ALL_AUTONOMOUS_PAYMENTS` or isolate affected tenant.
  2. Verify payment provider ledger via `PaymentReconciler.reconcile(...)` (§43). Never blindly retry.
  3. Inspect audit ledger tamper proof via `AuditLedger.verify_chain(...)`.

### Severity 2 (High) — Policy / Inventory Discrepancy
- **Triggers**:
  - Sudden inventory depletion or phantom reservation attempt (`INVENTORY_MISMATCH`).
  - Cross-tenant access denial spike (`CROSS_TENANT_ACCESS_DENIED`).
- **Response**:
  1. Refresh catalog inventory cache directly from verified merchant feed.
  2. Review tenant access logs.

### Severity 3 (Medium) — Operational Degraded Mode
- **Triggers**:
  - LLM / JEV latency spike or degraded confidence scores.
- **Response**:
  1. Fail closed to deterministic spending policy: transactions above autonomous threshold route to human review queue.

---

## 4. Post-Incident Recovery & Resume Procedure
1. Verify root cause mitigation with peer security review.
2. Verify all pending payments are resolved (captured, cancelled, or refunded).
3. Execute `POST /api/v1/emergency/resume` with operator ID.
4. Record formal post-mortem in `docs/security/incident-postmortems/`.

# AgentPay Incident Response Runbook (§21, §67, §176)

> **Document Status**: Production Standard  
> **Target Audience**: Incident Commanders (IC), SecOps Engineers, Site Reliability Engineers (SRE), Platform On-Call  
> **Governing Specifications**: `AGENTS.md` (§21, §64, §66, §67, §176, §177), ADR-0001, ADR-0002  
> **Last Verified**: Sprint 12 Release Hardening (2026-10-07)  

---

## 1. Incident Command & Authority Framework

AgentPay is a high-assurance autonomous commerce control plane. Because autonomous agent operations involve real financial commitments, incident triage must balance financial risk containment with operational continuity.

### 1.1 Command Roles & Responsibilities

| Role | Responsibility | Authority Limits |
|---|---|---|
| **Incident Commander (IC)** | Owns incident lifecycle, communications, and triage pace. Declares severity. | Authorized to order the process-global emergency pause (`POST /api/v1/emergency/pause`). The pause is an in-process flag — there is no multi-instance propagation. |
| **Security Operations Lead (SecOps)** | Investigates adversarial exploits, prompt injections, privilege escalations, and tamper alarms. | Authorized to quarantine or revoke agents (`AgentStatus.QUARANTINED`, `REVOKED`) and isolate tenant partitions. |
| **Engineering / SRE Lead** | Diagnoses system faults, database bottlenecks, saga compensation failures, and provider timeouts. | Authorized to deploy fixes, execute payment reconciliations (`PaymentReconciler`), and trigger recovery pipelines. |

### 1.2 Decision Hierarchy for Emergency Interventions

1. **Life & Financial Safety First**: Any detected price manipulation, unauthorized limit expansion, or uncontained loop anomaly triggers immediate containment.
2. **Narrowest Viable Blast Radius**: If the anomaly is isolated to a single agent or tenant, prefer Agent Quarantine or Tenant Isolation before engaging the global pause.
3. **Fail-Closed Default (§2.4)**: If the integrity of identity, spending limits, inventory, or payment status is unknown, operations must fail closed. Never assume a transaction succeeded without cryptographic or provider confirmation.

---

## 2. Emergency Kill Switch Operation (§21, §67)

The AgentPay emergency kill switch halts all mutating and financial operations process-wide, while preserving non-mutating read access and inspection. The pause is global: there is no per-tenant pause switch. Per-tenant containment means quarantining or suspending the affected agent (`AgentStatus.QUARANTINED`, `SUSPENDED`, `REVOKED`).

### 2.1 Kill Switch Control Plane Endpoints

All emergency endpoints require operator authentication and attribution (§67).

| Operation | HTTP Method & Path | Payload / Query Parameters | Expected Status |
|---|---|---|---|
| **Check Status** | `GET /api/v1/emergency/status` | None | `200 OK` |
| **Engage Pause** | `POST /api/v1/emergency/pause` | `{"operator_id": str, "reason": str, "scope": "ALL"}` | `200 OK` |
| **Disengage / Resume** | `POST /api/v1/emergency/resume` | `{"operator_id": str}` | `200 OK` |

### 2.2 Operator Invocation Commands (cURL & Shell)

#### A. Checking Kill Switch Status
```bash
curl -s -X GET "http://localhost:8000/api/v1/emergency/status" \
  -H "X-Operator-ID: secops_lead_01" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json"
```
*Expected 200 Response*:
```json
{
  "paused": false,
  "emergency_paused": false,
  "metadata": {}
}
```

#### B. Engaging Cluster-Wide Kill Switch
```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/pause" \
  -H "X-Operator-ID: secops_lead_01" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "secops_lead_01",
    "reason": "Active price drift anomaly detected across multiple merchant catalogs - Sev 1 triage",
    "scope": "ALL"
  }'
```
*Expected 200 Response*:
```json
{
  "status": "PAUSED",
  "paused": true,
  "emergency_paused": true,
  "metadata": {
    "paused_at": "2026-10-07T19:30:00.000000Z",
    "paused_by": "secops_lead_01",
    "reason": "Active price drift anomaly detected across multiple merchant catalogs - Sev 1 triage",
    "scope": "ALL"
  }
}
```

#### C. Resuming Autonomous Operations
```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/resume" \
  -H "X-Operator-ID: secops_lead_01" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "secops_lead_01"
  }'
```
*Expected 200 Response*:
```json
{
  "status": "ACTIVE",
  "paused": false,
  "emergency_paused": false,
  "metadata": {
    "paused_at": "2026-10-07T19:30:00.000000Z",
    "paused_by": "secops_lead_01",
    "reason": "Active price drift anomaly detected across multiple merchant catalogs - Sev 1 triage",
    "scope": "ALL",
    "resumed_by": "secops_lead_01",
    "resumed_at": "2026-10-07T19:45:00.000000Z"
  }
}
```

### 2.3 Fail-Closed Interception Behavior

When the kill switch is active (`_emergency_paused = True`), the API router intercepts all mutating transactional requests and returns HTTP 503 `EMERGENCY_PAUSE_ACTIVE` (§13 error envelope):

```json
{
  "error": {
    "code": "EMERGENCY_PAUSE_ACTIVE",
    "message": "Autonomous payment processing is temporarily suspended by emergency control",
    "request_id": "req_8f192b0c-4e12"
  }
}
```

#### Blocked Transactional Routes
- Action Requests: `POST /api/v1/actions`, `POST /agent/actions`
- ACP Checkout Protocol: `POST /api/v1/protocols/acp/checkout`
- AP2 Mandate Protocol: `POST /api/v1/protocols/ap2/mandates`
- UAP JSON-RPC Protocol: `POST /api/v1/protocols/uap/rpc`
- x402 Micropayment Protocol: `POST /api/v1/protocols/x402/pay`

### 2.4 Survival of Safe Read-Only Endpoints (§21)

To allow continuous diagnostics, monitoring, and operator review while payments are blocked, the following routes remain fully accessible (HTTP 200):
- **Health Check**: `GET /api/v1/health` (reports `"emergency_paused": true` alongside dependency status)
- **OpenAPI Schema**: `GET /openapi.json`
- **Product Discovery**: `GET /agent/catalog`, `GET /agent/products/{product_id}`
- **Inventory Check**: `GET /agent/products/{variant_id}/inventory`
- **Approval Review & Inspection**: Reviewed via the Web Control Center dashboard at `http://localhost:5173` / `apps/web/` (§66). Mutation routes (`POST /api/v1/approvals/{approval_id}/approve` and `POST /api/v1/approvals/{approval_id}/reject`) require explicit human operator invocation.

---

## 3. Severity & SLA Matrix

| Severity Level | Definition & Triggers | Target Acknowledgment | Target Containment | Target Resolution | Mandatory Actions |
|---|---|---|---|---|---|
| **Sev 1 (Critical)** | Active or suspected financial breach, price manipulation, privilege escalation attempt, audit ledger tamper detection, or uncompensated payment gateway timeout. | < 5 min | < 15 min | < 4 hours | 1. Page IC and SecOps immediately.<br>2. Invoke Kill Switch (`PAUSE`).<br>3. Freeze tenant/agent permissions.<br>4. Reconcile payment ledger. |
| **Sev 2 (High)** | Inventory depletion race conditions, elevated cross-tenant authorization rejections (>5% error budget), catalog price drift preflight aborts, or saga compensation retry exhaustion. | < 15 min | < 30 min | < 8 hours | 1. Page SRE and Engineering Lead.<br>2. Drain and refresh catalog cache.<br>3. Inspect tenant repository locks.<br>4. Trigger background compensation workers. |
| **Sev 3 (Medium)** | Advisory AI / JEV inference timeout spike, non-critical telemetry degradation, or human approval queue backlog (>100 items). | < 30 min | < 2 hours | < 24 hours | 1. Route to on-call queue.<br>2. Fail closed to deterministic policy rules.<br>3. Notify merchant operations. |

---

## 4. Incident Response Playbooks

### Playbook A: Agent Privilege Escalation or Loop Anomaly (§64)

**Symptom**: Alerts on `PrivilegeEscalationAttemptError` (e.g., agent attempting administrative action `set_spending_limit`), or `LoopBudgetExceededError` (agent exceeding 5 tool calls or recursion depth of 3).

1. **Immediate Identification**:
   Query audit logs for the rogue agent:
   ```bash
   # Filter events where actor matches agent and event indicates escalation
   grep -i "privilege_escalation" /var/log/agentpay/audit.log
   ```
2. **Quarantine or Revoke the Agent**:
   Transition agent lifecycle state to `QUARANTINED` or `REVOKED` in `domain/identity.py`:
   ```python
   agent = agent_repo.get(tenant_id, agent_id)
   agent.transition_to(AgentStatus.QUARANTINED)
   agent_repo.save(tenant_id, agent)
   ```
   *Note*: A quarantined agent immediately fails closed on all action requests with `AgentQuarantinedError`.
3. **Investigate Loop Budget Violation**:
   - Inspect `AgentLoopTracker` metrics.
   - Verify whether the agent is caught in an external tool invocation cycle or adversarial agent-to-agent prompt loop.
4. **Resolution**:
   Update agent capability definitions or revoke credentials permanently.

---

### Playbook B: Price Manipulation / Catalog Discrepancy (§32, §33)

**Symptom**: Surge in `PRICE_MISMATCH` or `ConflictType.PRICE_MISMATCH` rejections during `EvidenceConflictDetector` pre-flight checks.

1. **Verify Evidence Pipeline**:
   Examine audit record for conflicting claims:
   ```json
   {
     "conflict_type": "PRICE_MISMATCH",
     "severity": "CRITICAL",
     "evidence_source": "merchant_catalog_feed",
     "asserted_amount": {"minor_units": 100000, "currency": "INR"},
     "verified_amount": {"minor_units": 149900, "currency": "INR"}
   }
   ```
2. **Verify Merchant Catalog Integrity**:
   - Confirm if merchant issued a genuine price change without invalidating agent cached quotes.
   - Confirm whether external scraper/feed was manipulated or injected with tampered payload.
3. **Cache Invalidation**:
   Drain stale catalog cache for the affected merchant:
   ```bash
   # Purge tenant merchant catalog keys
   redis-cli -n 0 DEL "tenant:tenant_01:catalog:*"
   ```
4. **Enforce Pre-flight Re-verification**:
   Ensure `PurchaseOrchestrator` re-fetches verified variant price before executing payment.

---

### Playbook C: Payment Timeout & Saga Compensation Failure (§41, §43)

**Symptom**: Payment gateway returns timeout, network socket disconnect, or status `UNKNOWN`, leaving order in indeterminate state.

1. **Enforce Non-Negotiable Invariant**:
   **NEVER BLINDLY RETRY A CHARGE (§43, §0.4).** Repeating a charge without querying provider state risks severe double-debiting.
2. **Execute Payment Reconciliation (`PaymentReconciler`)**:
   Query the provider for the authoritative status of the UNKNOWN attempt — never re-charge.
   Real API (`agentpay.application.services.reconciliation_service`):
   ```python
   reconciler = PaymentReconciler(payment_provider=payment_provider, payment_repo=payment_repo)
   attempt = reconciler.reconcile_unknown_payment(tenant_id=tenant_id, attempt_id=attempt_id)
   ```
3. **Execute Saga Compensation**:
   - If the provider reports `CAPTURED` but local order creation failed: issue a saga refund with
     the real port signature:
     ```python
     payment_provider.refund(
         tenant_id=tenant_id, attempt_id=attempt_id, amount=amount, reason="saga compensation"
     )
     ```
   - If the provider reports `FAILED` or the attempt is unknown: mark the local payment attempt as
     `FAILED` and release reserved inventory with the real repository signature:
     ```python
     inv_repo.release(tenant_id=tenant_id, variant_id=variant_id, qty=qty)
     ```
4. **Notify Buyer & Update Ledger**:
   Emit the audit event matching the reconciled outcome — `PAYMENT_CAPTURED`, `PAYMENT_FAILED`,
   or `PAYMENT_UNKNOWN` (there is no `PAYMENT_RECONCILED` event type).

---

### Playbook D: Audit Ledger Tamper Alert (§45, §46)

**Symptom**: `AuditLedger.verify_chain(tenant_id)` returns `False`, or continuous hash verification raises `AuditChainIntegrityError`.

1. **Immediate Freeze**:
   Engage emergency kill switch immediately (`scope = "ALL"` or `scope = tenant_id`).
2. **Identify Breach Location**:
   Execute diagnostic chain trace:
   ```python
   events = audit_ledger.get_events(tenant_id)
   for i, event in enumerate(events):
       expected_prev = GENESIS_HASH if i == 0 else events[i-1].event_hash
       if event.previous_event_hash != expected_prev:
           print(f"Broken linkage at index {i}: ID={event.event_id}")
       recomputed = AuditHasher.compute_event_hash(event.data, event.previous_event_hash)
       if event.event_hash != recomputed:
           print(f"Content or metadata tampered at index {i}: ID={event.event_id}")
   ```
3. **Isolate Affected Storage Node**:
   - Check write-ahead logs, WORM storage replication, and database mutation logs.
   - Determine if tampering occurred via direct DB update, unauthenticated SQL injection, or hardware bit-flip.
4. **Restore Certified Snapshot**:
   Execute Point-in-Time Recovery from immutable WORM replication (see `docs/operations/backup-restore.md`).

---

## 5. Recovery, Resumption & Post-Mortem

### 5.1 Four-Step Resumption Protocol

Before executing `POST /api/v1/emergency/resume`, the Incident Commander must formally verify:

1. **Root Cause Neutralized**: The vulnerability, misconfigured policy, or rogue agent is patched, revoked, or quarantined.
2. **Ledger Integrity Verified**: `AuditLedger.verify_chain()` returns `True` across all active tenant partitions.
3. **Payment State Clean**: Zero payments remain in status `UNKNOWN`; all pending transactions are either `CAPTURED` with confirmed orders or `REFUNDED` with released inventory.
4. **Verification Gates Clean**: Full verification suite passes with exit code 0 (`ruff`, `mypy`, `pytest`, `web.test.mjs`).

### 5.2 Blameless Post-Mortem Template

Every Sev 1 or Sev 2 incident requires a published post-mortem within 48 hours in `docs/security/incident-postmortems/YYYY-MM-DD-<slug>.md`:

```markdown
# Incident Post-Mortem: [TITLE]

## Metadata
- **Date / Time (UTC)**: YYYY-MM-DD HH:MM to HH:MM UTC
- **Severity**: Sev 1 / Sev 2
- **Incident Commander**: [Name/Role]
- **SecOps Lead**: [Name/Role]
- **Impacted Tenants**: [Tenant IDs or "Cluster-Wide"]
- **Financial Exposure**: ₹[Amount] (Total minor units)

## Executive Summary
[High-level description of what occurred, why, and how it was contained.]

## Timeline (UTC)
- **HH:MM** - Anomaly detected by [Alert/Monitoring].
- **HH:MM** - Kill switch engaged via `POST /api/v1/emergency/pause`.
- **HH:MM** - Root cause identified as [Description].
- **HH:MM** - Containment fix deployed.
- **HH:MM** - Ledger integrity verified (`verify_chain() == True`).
- **HH:MM** - Kill switch disengaged via `POST /api/v1/emergency/resume`.

## Root Cause Analysis (5 Whys)
1. Why did the issue occur?
2. Why did the preliminary check fail?
3. Why was the condition not caught in staging?
4. Why did the metric alert trigger late?
5. Why was the policy engine unable to block it earlier?

## Corrective Actions & Prevention
| Action Item | Type (Prevent/Detect/Mitigate) | Owner | Target Date |
|---|---|---|---|
| Add regression test in test_adversarial_threat_harness.py | Prevent | SecOps | YYYY-MM-DD |
| Tune alerting threshold on price discrepancy | Detect | SRE | YYYY-MM-DD |
```

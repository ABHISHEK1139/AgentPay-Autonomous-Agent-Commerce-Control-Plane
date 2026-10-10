# Cross-Tenant Security Incident Runbook (§49, §50, §51, §52, §133)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-006`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 5m, MTTC ≤ 10m, MTTR ≤ 2h)  
> **Service / Module**: `agentpay.domain.identity`, `agentpay.api.app`, `agentpay.domain.errors`  
> **Author / Owner**: SecOps Lead & Data Protection Officer (DPO)  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§49, §50, §51, §52, §133), Vector 03, OWASP API1 (BOLA)  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay is a multi-tenant platform designed to serve multiple sovereign enterprises and organizations. Cross-tenant isolation is absolute: no agent, human user, or API caller may access, discover, or mutate resources belonging to another tenant under any circumstance.

This runbook specifies:
- Multi-tenant partitioning invariants across repository, cache, and authentication layers.
- Detection alerts and diagnostic triage procedures for `CrossTenantAccessError` and BOLA attempts.
- Immediate quarantine and containment actions against offending callers.
- Forensic investigation protocols verifying target tenant data integrity.
- Privacy compliance and statutory reporting under the India DPDP Act and GDPR.

---

## 2. Multi-Tenant Security Architecture (§49, §50)

AgentPay enforces strict logical and cryptographic multi-tenancy:
1. **Explicit `tenant_id` on Every Query**: Every repository method requires `tenant_id` as its primary partitioning key (`repo.get(tenant_id, id)`). No query executes without tenant scoping.
2. **Per-Tenant Partition Concurrency Locks**: In-memory and cache stores maintain segregated locks (`_locks[tenant_id]`) preventing cross-tenant thread contention.
3. **Cryptographic Credential Binding**: API tokens and keys are cryptographically bound to a single `tenant_id`. Submitting an assertion for `tenant_B` using a token bound to `tenant_A` immediately raises `CrossTenantAccessError` (HTTP 403 `CROSS_TENANT_ACCESS_DENIED`).
4. **Cache Key Segregation**: All cache entries follow the format `tenant:{tenant_id}:{entity}:{id}`.

---

## 3. Trigger Conditions & Detection Alarms

- **`CrossTenantAccessError` Alarm**: Telemetry alert triggers whenever an API caller attempts to query or mutate a resource belonging to a different tenant partition.
- **Credential-Header Mismatch**: HTTP request presents `Authorization` bearer token issued to Tenant A but asserts `X-Tenant-ID: tenant_B`.
- **Entity ID Enumeration (BOLA/IDOR)**: Rapid sequential requests attempting foreign resource IDs across foreign tenants.
- **Cache Namespace Bleed**: Detection of cache key missing tenant prefix.

---

## 4. Diagnostic Steps

### 4.1 Search Telemetry for Cross-Tenant Denials via PowerShell
```powershell
Get-Content -Path "C:\var\log\agentpay\api_access.log" -Tail 5000 | 
    Select-String -Pattern "CROSS_TENANT_ACCESS_DENIED" | 
    Select-String -Pattern "trace_id="
```

### 4.2 Extract Forensic Attributes
From the access log, extract:
- Offending Principal / Actor ID
- Source IP address and Client User-Agent
- Asserted `X-Tenant-ID` vs Credential `tenant_id`
- Target Resource ID attempted
- Trace ID and Request Timestamp

### 4.3 Verify Boundary Check Response
Confirm that the boundary held and returned standard RFC 403 envelope:

```json
{
  "error": {
    "code": "CROSS_TENANT_ACCESS_DENIED",
    "message": "Credential tenant 'tenant_01' does not match asserted tenant 'tenant_02'",
    "request_id": "req_11a001",
    "trace_id": "trace_9901",
    "details": {
      "credential_tenant": "tenant_01",
      "asserted_tenant": "tenant_02"
    }
  }
}
```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Immediate Offending Actor Quarantine

If deliberate enumeration or malicious exploit attempt is confirmed:
1. Revoke the offending credential immediately via Python:
   ```python
   from agentpay.application.ports.identity_port import CredentialRepository

   def quarantine_actor_credential(cred_repo: CredentialRepository, tenant_id: str, cred_id: str):
       cred = cred_repo.get(tenant_id, cred_id)
       if cred:
           revoked = cred.revoke(reason="Cross-tenant access exploit attempt detected")
           cred_repo.save(tenant_id, revoked)
           print(f"Credential {cred_id} revoked.")
   ```
2. If an agent initiated the attack, quarantine the agent service principal:
   ```powershell
   $headers = @{
       "Authorization" = "Bearer $env:OPERATOR_TOKEN"
       "X-Tenant-ID"   = "tenant_01"
       "Content-Type"  = "application/json"
   }
   $body = @{
       status = "QUARANTINED"
       reason = "Cross-tenant BOLA enumeration attempt"
   } | ConvertTo-Json

   Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agents/ag_attacker/status" `
       -Method Post -Headers $headers -Body $body
   ```

### Step 2: Invalidate Cache Partitions in Redis

If cache pollution across tenants is suspected, purge all cache keys for the source partition:

```powershell
& "redis-cli.exe" KEYS "tenant:tenant_01:*" | ForEach-Object {
    & "redis-cli.exe" DEL $_
}
```

### Step 3: Forensic Inspection of Target Tenant Ledger

Verify that the target tenant suffered zero unauthorized modifications:
1. Export target tenant audit stream.
2. Run cryptographic verification tool:
   ```powershell
   uv run python scripts/verify_audit_chain.py C:\var\log\agentpay\tenant_02_audit.jsonl
   ```
   Confirm exit code 0 (`SUCCESS`).
3. Verify that zero orders, payment attempts, or approvals were created during the incident window.

---

## 6. Verification & Health Restoration

1. Verify that subsequent requests from the attacker fail with HTTP 401 `UNAUTHENTICATED`.
2. Confirm that legitimate users of both Tenant A and Tenant B continue processing normal requests without cross-talk or disruption.
3. Review firewall / WAF rules to block the attacker's source IP range.

---

## 7. Audit Trail Accounting & Post-Mortem

Every cross-tenant access denial generates a security audit event:
- **Event Type**: `AuditEventType.POLICY_EVALUATED` / `SECURITY_EVENT`
- **Data Payload**:
```json
{
  "event": "CROSS_TENANT_ACCESS_DENIED",
  "source_tenant": "tenant_01",
  "target_tenant": "tenant_02",
  "attempted_resource": "prod_foreign_99",
  "actor": "usr_attacker",
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 7.1 Statutory & Privacy Obligations
- **India DPDP Act (2023)**: If personal data belonging to another tenant was accessed, submit a formal breach report to the Data Protection Board within statutory deadlines.
- **CERT-In Cyber Security Directions**: Report unauthorized access incidents within 6 hours of discovery.

### 7.2 5-Whys Post-Mortem Template
1. **Why was the cross-tenant alarm triggered?** Client token for Tenant A submitted a request asserting Tenant B.
2. **Why was the request attempted?** Vulnerability scanning or credential confusion in multi-tenant client app.
3. **Why was access blocked?** System invariant §49 enforces credential-tenant binding at the API gateway boundary.
4. **Why was data leakage prevented?** All repository interfaces enforce `repo.get(tenant_id, id)` partition filters.
5. **How will recurrence be prevented?** Block repeated cross-tenant origin IPs at the edge WAF after 3 failed attempts.

---

## 8. Related References & Runbooks
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md) — Agent quarantine and revocation.
- [08-key-rotation-and-credential-revocation-runbook.md](file:///docs/runbooks/08-key-rotation-and-credential-revocation-runbook.md) — Credential revocation.
- [docs/operations/incident-runbook.md](file:///docs/operations/incident-runbook.md) — Incident command procedures.

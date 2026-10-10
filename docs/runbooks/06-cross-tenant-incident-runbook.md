# Cross-Tenant Security Incident Runbook (§49, §50, §51, §52, §133)

> **Document Status**: Production Standard  
> **Target Audience**: SecOps Lead, Data Protection Officer (DPO), Platform Security Engineers  
> **Governing Specifications**: `AGENTS.md` (§49, §50, §51, §52, §133), Vector 03, OWASP API1 (BOLA)  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Multi-Tenant Security Architecture (§49, §50)

AgentPay enforces strict logical and cryptographic multi-tenancy across all layers:
1. **Explicit `tenant_id` on Every Query**: Every repository port requires `tenant_id` as its primary partitioning key (`repo.get(tenant_id, id)`). No query executes without tenant scoping.
2. **Per-Tenant Partition Locks**: In-memory and cache stores maintain segregated locks (`_locks[tenant_id]`) preventing cross-tenant thread contention.
3. **Cryptographic Credential Binding**: API tokens and keys are cryptographically bound to a single `tenant_id`. Submitting an assertion for `tenant_B` using a token bound to `tenant_A` immediately raises `CrossTenantAccessError` (HTTP 403 `CROSS_TENANT_ACCESS_DENIED`).
4. **Cache Key Segregation**: All cache entries follow the format `tenant:{tenant_id}:{entity}:{id}`.

---

## 2. Trigger Conditions & Detection Alarms

- **`CrossTenantAccessError` Alarm**: Telemetry alert triggers whenever an API caller attempts to query or mutate a resource belonging to a different tenant.
- **Header Mismatch**: HTTP request presents `Authorization` bearer token issued to Tenant A but asserts `X-Tenant-ID: tenant_B`.
- **Entity ID Enumeration (BOLA)**: Rapid sequential requests attempting foreign resource IDs.

---

## 3. Step-by-Step Triage & Containment Protocol

### Step 1: Immediate Triage & Boundary Verification

1. Inspect the access log for the `trace_id` associated with the alert:
   ```bash
   grep "trace_id=trace_9901" /var/log/agentpay/api_access.log
   ```
2. Verify that the boundary check held:
   - Confirm the client received HTTP 403 `CROSS_TENANT_ACCESS_DENIED`.
   - Confirm zero entity bytes were returned to the caller.

Standard RFC 403 Error Envelope:
```json
{
  "error": {
    "code": "CROSS_TENANT_ACCESS_DENIED",
    "message": "Credential tenant 'tenant_01' does not match asserted tenant 'tenant_02'",
    "request_id": "req_11a001",
    "trace_id": "trace_9901"
  }
}
```

### Step 2: Offending Actor Quarantine

If deliberate enumeration or malicious exploit attempt is confirmed:
1. Immediately quarantine or revoke the offending agent/user credential:
   ```python
   cred_repo.revoke(tenant_id=source_tenant, credential_id=source_cred_id, reason="Cross-tenant exploit attempt")
   ```
2. If widespread compromise is suspected, suspend the entire source tenant partition.

### Step 3: Cache Invalidation

If cache pollution across tenants is suspected, purge all cache keys for the affected partitions:
```bash
redis-cli --eval "return redis.call('del', unpack(redis.call('keys', 'tenant:' .. ARGV[1] .. ':*')))" 0 "<source_tenant>"
```

---

## 4. Forensic Investigation & Privacy Reporting

1. **Query Target Tenant Ledger**: Verify that no unauthorized transactions or state transitions were committed on the target tenant during the incident window.
2. **DPDP / GDPR Assessment**: If unredacted personal data or transactional records of another organization were exposed, notify the Data Protection Officer within 1 hour for statutory breach notification (e.g. CERT-In 6-hour reporting window).

---

## 5. Audit Logging

Every cross-tenant access denial generates a security audit event:
- **Event Type**: `AuditEventType.POLICY_EVALUATED` / Security Event
- **Payload**:
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

---

## 6. Cross-References
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md)
- [08-key-rotation-and-credential-revocation-runbook.md](file:///docs/runbooks/08-key-rotation-and-credential-revocation-runbook.md)

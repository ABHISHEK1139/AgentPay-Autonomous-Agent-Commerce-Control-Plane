# Key Rotation & Credential Revocation Runbook (§14, §15, §16, §56, §73, §133)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-008`  
> **Severity / SLA**: Sev 1 for Emergency Leak (MTTA ≤ 5m, MTTC ≤ 10m) / Sev 3 for Scheduled Rotation (SLA ≤ 2 business days)  
> **Service / Module**: `agentpay.domain.identity`, `agentpay.application.ports.identity_port`, `agentpay.adapters.in_memory.identity_repository`  
> **Author / Owner**: Platform Security Engineers & IAM Administrators  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§14, §15, §16, §56, §73, §133), RFC 9700, NIST SP 800-57  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay requires cryptographic authentication on all non-public API routes. Credentials represent the identity of human principals, autonomous agents, and system operators. To maintain defensible security under NIST SP 800-57 guidelines, credentials must undergo scheduled periodic rotation, while compromised keys must be revoked immediately without disrupting unaffected tenant services.

This runbook specifies:
- The credential data model, cryptographic hashing, and tenant binding architecture.
- The 4-step zero-downtime dual-key rotation procedure for active autonomous agents.
- The emergency immediate revocation protocol for leaked or compromised credentials.
- Diagnostic commands in PowerShell, bash, and Python to inspect credential lifecycles.
- Verification checks ensuring revoked keys are rejected with HTTP 401 `UNAUTHENTICATED`.

---

## 2. Credential Model & Architecture (§14, RFC 9700)

1. **Credential Types Supported**:
   - API Key (`X-API-Key: <key>`)
   - Bearer Token (`Authorization: Bearer <token>`)
   - HMAC Signatures (`X-Signature: <hex>`)
2. **Immutable Binding**: Every credential is cryptographically bound to:
   - `tenant_id`: Mandatory tenant partition ID.
   - `principal_id`: Unique ID of the human user or agent.
   - `credential_type`: Enum indicating key type.
   - `scopes`: Tuple of permitted action strings (e.g. `commerce:read`, `commerce:write`).
   - `expires_at`: ISO 8601 UTC timestamp; expired keys fail closed immediately.
   - `status`: Enum (`ACTIVE`, `EXPIRED`, `REVOKED`). `REVOKED` is terminal.
3. **Zero Plaintext Secrets in Storage**: The database and repositories store ONLY SHA-256 hashes of credential secrets (`secret_hash = SHA256(raw_secret)`). Raw keys are shown to operators exactly once upon generation.

---

## 3. Trigger Scenarios

- **Scheduled 90-Day Rotation Cycle**: Mandatory compliance rotation per NIST SP 800-57 guidelines.
- **Emergency Leak / Compromise**: Credential accidentally committed to git, exposed in public telemetry, or logged in client crash reports.
- **Agent Service Principal Decommissioning**: Sunset of an automated workflow or agent offboarding.
- **Employee / Contractor Offboarding**: Immediate revocation of operator credentials.

---

## 4. Diagnostic Steps

### 4.1 Inspect Active Credentials via Python
```python
from agentpay.adapters.in_memory.identity_repository import InMemoryCredentialRepository

cred_repo = InMemoryCredentialRepository()
creds = cred_repo.list_by_principal("tenant_01", "ag_checkout_bot")
print(f"Active credentials for agent: {len(creds)}")
for c in creds:
    print(f"  ID: {c.credential_id}, Type: {c.credential_type.value}, Status: {c.status.value}, Expires: {c.expires_at}")
```

### 4.2 Inspect Credential Verification via PowerShell
```powershell
$headers = @{
    "Authorization" = "Bearer $env:PROBE_TOKEN"
    "X-Tenant-ID"   = "tenant_01"
}
try {
    $res = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/verify" -Method Get -Headers $headers
    Write-Host "Token VALID. Principal: $($res.principal_id), Scopes: $($res.scopes)"
} catch {
    Write-Host "Token INVALID: HTTP $($_.Exception.Response.StatusCode.value__)"
}
```

---

## 5. Mitigation & Recovery Playbook

### Procedure A: Zero-Downtime Dual-Key Rotation Strategy

To prevent downtime for active autonomous agents, execute a dual-key overlap rotation:

```
T0: key_v1 Active
T1: Generate key_v2 (Active) ─── Both keys valid during 48h overlap ───
T2: Deploy key_v2 to autonomous agent configuration
T3: Verify traffic on key_v1 drops to zero in telemetry
T4: Hard revoke key_v1 (Terminal REVOKED)
```

#### Step 1: Provision New Replacement Key (`key_v2`)
```python
import uuid
from datetime import datetime, timedelta, UTC
from agentpay.domain.identity import Credential, CredentialType, CredentialStatus, UserRole
from agentpay.application.ports.identity_port import CredentialRepository

def provision_rotation_key(
    cred_repo: CredentialRepository,
    tenant_id: str,
    principal_id: str,
    role: UserRole = UserRole.AGENT_OPERATOR,
    scopes: tuple[str, ...] = ("commerce:read", "commerce:write"),
) -> tuple[str, str]:
    raw_key = f"apk_{tenant_id}_{uuid.uuid4().hex}"
    secret_hash = Credential.hash_secret(raw_key)
    cred_id = f"cred_{uuid.uuid4().hex[:8]}"
    
    cred = Credential(
        credential_id=cred_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        credential_type=CredentialType.API_KEY,
        secret_hash=secret_hash,
        expires_at=datetime.now(UTC) + timedelta(days=90),
        status=CredentialStatus.ACTIVE,
        role=role,
        scopes=scopes,
    )
    cred_repo.save(tenant_id, cred)
    return cred_id, raw_key
```

#### Step 2: Deploy `key_v2` to Agent Vault / Secret Store
Deploy the raw key securely to the agent's secret store.

#### Step 3: Monitor Telemetry Migration
```powershell
Get-Content -Path "C:\var\log\agentpay\api_access.log" -Tail 2000 | 
    Select-String -Pattern "credential_id=cred_old"
```
Confirm that calls using the old key cease.

#### Step 4: Hard Revoke `key_v1`
```python
def revoke_old_key(cred_repo: CredentialRepository, tenant_id: str, old_cred_id: str):
    old_cred = cred_repo.get(tenant_id, old_cred_id)
    if old_cred:
        revoked = old_cred.revoke(reason="Scheduled 90-day rotation completed")
        cred_repo.save(tenant_id, revoked)
        print(f"Old key {old_cred_id} revoked.")
```

---

### Procedure B: Emergency Immediate Key Revocation Protocol

When a key compromise or secret leak is detected:
1. **Bypass the Overlap Window**: Do not wait for migration.
2. **Execute Immediate Revocation via PowerShell**:
   ```powershell
   $headers = @{
       "Authorization" = "Bearer $env:OPERATOR_TOKEN"
       "X-Tenant-ID"   = "tenant_01"
       "Content-Type"  = "application/json"
   }
   $body = @{
       reason = "Emergency revocation - public secret leak detected"
   } | ConvertTo-Json

   Invoke-RestMethod -Uri "http://localhost:8000/api/v1/credentials/cred_leaked/revoke" `
       -Method Post -Headers $headers -Body $body
   ```
3. **Invalidate Active Redis Sessions**:
   ```powershell
   & "redis-cli.exe" DEL "tenant:tenant_01:session:cred_leaked"
   ```

---

## 6. Verification & Health Restoration

1. **Verify Leaked Key Rejection**:
   Submit a probe request presenting the revoked key and confirm HTTP 401 `UNAUTHENTICATED`:
   ```json
   {
     "error": {
       "code": "UNAUTHENTICATED",
       "message": "Credential has been revoked",
       "request_id": "req_0192a01",
       "trace_id": "trace_8819201"
     }
   }
   ```
2. **Verify Replacement Key Access**:
   Submit a probe request presenting `key_v2` and confirm HTTP 200 OK.
3. **Secret Scanning**:
   Run `git log -S "apk_" --all` to ensure no raw secrets were committed to source control.

---

## 7. Audit Trail Accounting & Post-Mortem

Credential lifecycle transitions emit security audit events:
- **Event Type**: `AuditEventType.SECURITY_EVENT`
- **Data Payload**:
```json
{
  "event": "CREDENTIAL_REVOKED",
  "credential_id": "cred_leaked",
  "tenant_id": "tenant_01",
  "principal_id": "ag_checkout_bot",
  "revoked_by": "secops_lead_01",
  "reason": "Emergency revocation - public secret leak detected",
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 5-Whys Post-Mortem Template (Compromised Key)
1. **Why was emergency revocation required?** API key secret appeared in an unredacted support ticket log.
2. **Why was the key logged?** Client SDK debug logging included the raw `Authorization` header.
3. **Why did the leak not lead to financial loss?** Key was revoked within 8 minutes; daily spending cap on the agent was ₹5,000.
4. **Why was the key logging not caught earlier?** Log scrubbing regex lacked matching for the `apk_*` prefix.
5. **How will recurrence be prevented?** Update client SDK to sanitize auth headers; deploy automated secret scanning pre-commit hooks.

---

## 8. Related References & Runbooks
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md) — Revoking the agent identity itself.
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Emergency cluster freeze.

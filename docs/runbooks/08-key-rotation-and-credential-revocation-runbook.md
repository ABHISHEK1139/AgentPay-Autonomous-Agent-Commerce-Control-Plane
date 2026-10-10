# Key Rotation & Credential Revocation Runbook (§14, §15, §16, §56, §73, §133)

> **Document Status**: Production Standard  
> **Target Audience**: Platform Security Engineers, SRE, Identity & IAM Administrators  
> **Governing Specifications**: `AGENTS.md` (§14, §15, §16, §56, §73, §133), RFC 9700, NIST SP 800-57  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Credential Model & Architecture (§14)

AgentPay requires cryptographic authentication on all non-public API endpoints.
- **Credential Types**: API Key (`X-API-Key: <key>`), Bearer Token (`Authorization: Bearer <token>`), and HMAC Signature headers.
- **Immutable Binding**: Every credential is strictly bound to `tenant_id`, `principal_id` (human user or agent), `expires_at`, `status`, and authorized permission `scopes`.
- **Zero Plaintext Secrets in Storage**: The database and repositories store only SHA-256 hashes of credential secrets (`secret_hash = SHA256(raw_secret)`).

---

## 2. Trigger Scenarios

- **Scheduled Rotation (90-Day Compliance Cycle)**: Periodic key rotation per NIST SP 800-57 standards.
- **Leaked / Compromised Key (Emergency Revocation)**: Credential accidentally published in code repository or telemetry.
- **Agent Decommissioning / Offboarding**: Service principal sunsetting.

---

## 3. Zero-Downtime Dual-Key Rotation Protocol

To prevent service disruption for active client agents, use a dual-key overlap window:

```
T0: key_v1 Active
T1: Generate key_v2 (Active) ─── Both keys valid during 48h overlap ───
T2: Update client agents to use key_v2
T3: Verify zero traffic on key_v1 in telemetry
T4: Revoke key_v1 (Terminal)
```

### Step 1: Provision New Key Version (`key_v2`)

Generate and store the replacement credential in the tenant repository:

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
) -> str:
    raw_key = f"apk_{tenant_id}_{uuid.uuid4().hex}"
    secret_hash = Credential.hash_secret(raw_key)
    cred = Credential(
        credential_id=f"cred_{uuid.uuid4().hex[:8]}",
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
    return raw_key  # Returned securely to operator for deployment
```

### Step 2: Deploy `key_v2` to Autonomous Agent Config
Update the agent's environment or vault secret store with `key_v2`.

### Step 3: Verify Traffic Migration
Inspect API access logs over 24–48 hours to confirm that incoming requests use `key_v2` and traffic to `key_v1` drops to zero.

### Step 4: Hard Revoke `key_v1`

Transition `key_v1` status to `REVOKED`:

```python
def revoke_old_key(
    cred_repo: CredentialRepository,
    tenant_id: str,
    old_credential_id: str,
    reason: str = "Scheduled 90-day key rotation completed",
) -> None:
    old_cred = cred_repo.get(tenant_id, old_credential_id)
    if old_cred is not None:
        revoked = old_cred.revoke(reason=reason)
        cred_repo.save(tenant_id, revoked)
```

---

## 4. Emergency Immediate Key Revocation Protocol

When a credential compromise is detected:
1. Bypass the overlap window immediately.
2. Call `cred.revoke(reason="Compromised key - emergency revocation")`.
3. Invalidate cached authentication sessions in Redis.
4. Verify that requests using the leaked key fail immediately with HTTP 401 `UNAUTHENTICATED`:
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

---

## 5. Audit Logging

Key generation, rotation, and revocation events emit security audit records:
- **Event Type**: `AuditEventType.AGENT_AUTHENTICATED` / Security Event
- **Payload**: Includes credential ID, principal ID, tenant ID, rotation phase, and operator ID. Never includes plaintext secrets or secret hashes.

---

## 6. Cross-References
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md)
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)
- [06-cross-tenant-incident-runbook.md](file:///docs/runbooks/06-cross-tenant-incident-runbook.md)

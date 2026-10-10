# Audit Chain Tamper Recovery Runbook (§29, §45, §46, §133, §177)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-004`  
> **Severity / SLA**: Sev 1 (MTTA ≤ 2m, MTTC ≤ 5m, MTTR ≤ 2h)  
> **Service / Module**: `agentpay.domain.audit`, `agentpay.adapters.in_memory.audit_ledger`, `scripts.verify_audit_chain`  
> **Author / Owner**: SecOps Lead & Platform Security Auditor  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§29, §45, §46, §133, §177), ADR-0001, Vector 14  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay records every state transition, authorization decision, policy evaluation, and financial execution in a cryptographically linked, append-only SHA-256 audit ledger. If an attacker tampers with a database row, backdates a timestamp, re-attributes an action to another agent, or alters monetary figures, the cryptographic linkage breaks immediately.

This runbook specifies:
- The mathematical hash chaining invariants and canonical preimage structure.
- Trigger conditions and forensic alarms for tamper detection.
- Emergency containment protocols to freeze ledger mutations.
- The forensic command-line procedure using `scripts/verify_audit_chain.py` to pinpoint breach offsets.
- Recovery procedures restoring ground truth from immutable WORM cloud object storage.

---

## 2. Cryptographic Hash Chain Architecture (§46)

### 2.1 Sequential Linking Formula
- **Genesis Hash**: The initial event in every tenant ledger chains from:
  $$\text{GENESIS\_HASH} = \text{"0"} \times 64$$
- **Sequential Linking Equation**:
  $$\text{EventHash}_n = \text{SHA256}(\text{CanonicalJSON}(\text{Preimage}_n) + \text{EventHash}_{n-1})$$
- **Canonical Preimage Fields**:
  Every preimage is deterministically serialized from 7 immutable fields sorted alphabetically:
  `action_id`, `actor`, `agent_id`, `data`, `event_type`, `tenant_id`, `timestamp`.
- **Sensitive Field Redaction (§40)**:
  Before computing the preimage hash, `SensitiveFieldRedactor` strips passwords, API keys, tokens, PAN, and CVV, replacing them with `"[REDACTED]"`. Decision summaries and evidence references are stored—never private LLM chain-of-thought (§29).

---

## 3. Trigger Conditions & Detection Alarms

- **Integrity Check Failure**: In-memory `AuditLedger.verify_chain(tenant_id)` returns `False`.
- **CLI Verification Failure**: Automated monitoring cron running `scripts/verify_audit_chain.py` exits with status `1`.
- **Preimage Hash Mismatch**: Stored `event_hash` differs from recomputed SHA-256 over canonical JSON preimage.
- **Linkage Breakage**: Stored `previous_event_hash` does not match the preceding record's `event_hash`.
- **Database Modification Alert**: SIEM alarm fires on direct `UPDATE` or `DELETE` SQL queries against audit tables.

---

## 4. Diagnostic Steps

### 4.1 Run Cryptographic Forensic CLI Tool
Execute the verifier tool on the audit stream export to pinpoint the exact corrupted record:

#### Windows PowerShell:
```powershell
uv run python scripts/verify_audit_chain.py C:\var\log\agentpay\audit_events.jsonl
```

#### Bash:
```bash
uv run python scripts/verify_audit_chain.py /var/log/agentpay/audit_events.jsonl
```

*Example Forensic Output Detecting Tamper*:
```text
[VERIFY] Checking 450 audit events for tenant 'tenant_01'...
[ERROR] Chain verification FAILED at event #142 (event_id: evt_9120ab11)!
  Stored Previous Hash:  8f99a011ef02bc4412aa89bb331001ff9922aacc551100aa7788991122334455
  Predecessor Hash:      8f99a011ef02bc4412aa89bb331001ff9922aacc551100aa7788991122334455
  Recomputed Event Hash: 44bca12099aabbcc112233445566778899aabbcc112233445566778899aabbcc
  Stored Event Hash:     55d1109aaabbccdd112233445566778899aabbcc112233445566778899aabbcc (MISMATCH)
[TAMPER DETECTED] Payload 'data' or metadata mutated at index 142.
```

### 4.2 Python Diagnostic Inspection
```python
from agentpay.domain.audit import AuditHasher
from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger

ledger = InMemoryAuditLedger()
valid = ledger.verify_chain("tenant_01")
print(f"Audit ledger integrity valid: {valid}")
```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Immediate Freeze via Emergency Pause

Tampering indicates either malicious database modification, infrastructure compromise, or storage corruption. Immediately freeze all cluster mutations:

#### Via PowerShell:
```powershell
$headers = @{
    "Authorization" = "Bearer $env:OPERATOR_TOKEN"
    "X-Tenant-ID"   = "system"
    "Content-Type"  = "application/json"
}
$body = @{
    operator_id = "secops_lead_01"
    reason      = "Audit hash chain verification failure detected at event #142 - freezing cluster"
    scope       = "ALL"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/emergency/pause" -Method Post -Headers $headers -Body $body
```

### Step 2: Breach Origin & Threat Investigation
1. **Query Database Audit Logs**: Check PostgreSQL logs for unauthorized SQL statements modifying `audit_events` table rows.
2. **Examine Process Execution**: Check system logs for unauthorized SSH access or DBA tool connections during the divergence window.
3. **Inspect Disk & Memory**: Verify hardware ECC memory and storage health to rule out bit-flip corruption.

### Step 3: Point-in-Time Recovery from WORM Cloud Storage

AgentPay streams verified audit blocks to immutable WORM (Write Once, Read Many) cloud object storage (e.g. AWS S3 Object Lock or GCS Bucket Lock in Compliance Mode):

1. **Download Canonical WORM Block**:
   ```powershell
   aws s3 cp s3://agentpay-audit-worm-bucket/tenant_01/audit_chain_20261010.jsonl C:\restore\audit_chain_canonical.jsonl
   ```
2. **Re-Verify Restored Chain**:
   ```powershell
   uv run python scripts/verify_audit_chain.py C:\restore\audit_chain_canonical.jsonl
   ```
   Must return exit code 0 (`SUCCESS`).
3. **Restore Database Table**: Truncate corrupted records past event #142 and replay verified WORM segment into active database.

### Step 4: Verification & Resumption

1. Run verification across all active tenant ledgers to confirm 100% integrity:
   ```powershell
   uv run python scripts/verify_audit_chain.py C:\var\log\agentpay\active_audit.jsonl
   ```
2. Disengage emergency pause:
   ```powershell
   Invoke-RestMethod -Uri "http://localhost:8000/api/v1/emergency/resume" `
       -Method Post -Headers $headers -Body (@{ operator_id = "secops_lead_01" } | ConvertTo-Json)
   ```

---

## 6. Verification & Health Checks

1. Verify that `scripts/verify_audit_chain.py` exits with code 0 across all tenant files.
2. Verify that `GET /api/v1/emergency/status` returns `"paused": false`.
3. Submit a probe audit event and confirm that it chains cleanly from the restored predecessor hash.

---

## 7. Audit Trail Accounting & Post-Mortem

### 7.1 Statutory Non-Repudiation Notification
Tampering attempts with financial or audit ledgers trigger mandatory regulatory reporting:
- **India DPDP Act / CERT-In**: Report within 6 hours if unauthorized data modification is suspected.
- **PCI-DSS Requirement 10.5.5**: Document use of file-integrity monitoring and cryptographic hash chain recovery.

### 7.2 5-Whys Post-Mortem Template
1. **Why was the tamper alarm tripped?** Stored SHA-256 hash at event #142 did not match computed canonical preimage.
2. **Why was the record modified?** Unauthorized SQL query or disk sector corruption.
3. **Why did the system halt?** The sequential hash chaining invariant detected the divergence and alerted SecOps.
4. **Why was recovery possible without data loss?** Immutable WORM cloud storage preserved the authoritative preimage.
5. **How will recurrence be prevented?** Revoke DBA direct write access, enforce row-level security (RLS), and increase WORM sync frequency.

---

## 8. Related References & Runbooks
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Emergency pause protocol.
- [09-disaster-recovery-failover-runbook.md](file:///docs/runbooks/09-disaster-recovery-failover-runbook.md) — Disaster recovery failover.
- [docs/operations/backup-restore.md](file:///docs/operations/backup-restore.md) — Database and audit backup procedures.

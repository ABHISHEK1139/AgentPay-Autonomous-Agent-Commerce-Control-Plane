# Audit Chain Tamper Recovery Runbook (§29, §45, §46, §133, §177)

> **Document Status**: Production Standard  
> **Target Audience**: SecOps Lead, Platform Security Auditor, Incident Commander (IC), Compliance Officer  
> **Governing Specifications**: `AGENTS.md` (§29, §45, §46, §133, §177), ADR-0001, Vector 14  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Cryptographic Hash Chain Architecture (§46)

AgentPay records all security and transaction transitions in a cryptographically chained, immutable audit ledger.

### 1.1 Hash Chain Invariant
- **Genesis Predecessor**: First event chains from `GENESIS_HASH = "0" * 64` (64 zeros).
- **Sequential Linking Formula**:
  $$\text{EventHash}_n = \text{SHA256}(\text{CanonicalJSON}(\text{Preimage}_n) + \text{EventHash}_{n-1})$$
- **Preimage Fields**: `action_id`, `actor`, `agent_id`, `data`, `event_type`, `tenant_id`, `timestamp`.
- **Sensitive Field Redaction (§40)**: Pre-hashing sanitization strips PAN, CVV, passwords, and API keys, replacing them with `"[REDACTED]"`. Decision summaries are stored, never private chain-of-thought (§29).

---

## 2. Trigger Conditions & Detection Alarms

- **Verification Failure**: `AuditLedger.verify_chain(tenant_id)` returns `False`.
- **CLI Verification Alarm**: Scheduled or automated job running `scripts/verify_audit_chain.py` exits with status `1`.
- **Preimage Mismatch**: Stored `event_hash` differs from recomputed SHA-256 hash.
- **Linkage Breakage**: Stored `previous_event_hash` does not match the preceding record's `event_hash`.

---

## 3. Step-by-Step Incident Playbook

### Step 1: Immediate Freeze via Emergency Pause

Tampering indicates either unauthorized database tampering, compromised infrastructure, or a disk failure. Immediately engage the kill switch to prevent additional writes:

```bash
curl -s -X POST "http://localhost:8000/api/v1/emergency/pause" \
  -H "Authorization: Bearer <operator_token>" \
  -H "X-Tenant-ID: system" \
  -H "Content-Type: application/json" \
  -d '{
    "operator_id": "secops_lead_01",
    "reason": "Audit hash chain verification failure detected - freezing cluster",
    "scope": "ALL"
  }'
```

### Step 2: Run Cryptographic Forensic CLI

Execute the verifier tool on the exported audit stream to pinpoint the corruption:

```bash
uv run python scripts/verify_audit_chain.py /var/log/agentpay/audit_events.jsonl
```

*Expected Forensic Output on Tamper*:
```text
[VERIFY] Checking 450 audit events for tenant 'tenant_01'...
[ERROR] Chain verification FAILED at event #142 (event_id: evt_9120ab11)!
  Stored Previous Hash:  8f99a011...
  Predecessor Hash:      8f99a011...
  Recomputed Event Hash: 44bca120...
  Stored Event Hash:     55d1109a... (MISMATCH)
[TAMPER DETECTED] Payload 'data' or metadata mutated at index 142.
```

### Step 3: Identify Breach Origin
1. Check database query audit logs to determine who or what modified row `evt_9120ab11`.
2. Inspect for unauthorized SQL execution, privilege escalation, or hardware bit-rot.
3. Compare local ledger with immutable WORM backups.

---

## 4. Point-in-Time Recovery from WORM Object Storage

AgentPay archives audit events to immutable WORM (Write Once, Read Many) cloud storage (e.g. AWS S3 Object Lock or GCS Bucket Lock in Compliance Mode):

1. **Pull Authoritative Block**: Retrieve the verified audit segment from the WORM bucket up to the point of divergence.
2. **Re-hydrate Tenant Ledger**: Restore the verified records into the active audit store.
3. **Re-run Verifier**:
   ```bash
   uv run python scripts/verify_audit_chain.py /restore/verified_audit_events.jsonl
   ```
   Confirm exit code 0 (`SUCCESS`).

---

## 5. Resumption & Post-Mortem Obligations

1. Confirm all tenants pass `verify_chain() == True`.
2. Disengage emergency pause via `POST /api/v1/emergency/resume`.
3. Submit a statutory report to compliance and data protection officers detailing the tamper attempt and remediation.

---

## 6. Cross-References
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)
- [09-disaster-recovery-failover-runbook.md](file:///docs/runbooks/09-disaster-recovery-failover-runbook.md)
- [backup-restore.md](file:///docs/operations/backup-restore.md)

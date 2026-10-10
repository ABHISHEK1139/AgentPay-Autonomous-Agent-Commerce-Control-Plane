# Rate Limit & Abuse Mitigation Runbook (§52, §62, §63, §64, §133)

> **Document Status**: Production Standard  
> **Target Audience**: SRE Lead, Edge Gateway Engineers, SecOps On-Call  
> **Governing Specifications**: `AGENTS.md` (§52, §62, §63, §64, §133), Vector 07, OWASP API4  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Risk-Tiered Rate Limiting Framework (§62)

AgentPay protects against denial-of-service, runaway agent loops, and brute-force financial transactions through tiered, defensive rate limiting.

### 1.1 Rate Limit Tiers

| Tier Name | Scope | Default Limit | Purpose |
|---|---|---|---|
| **Public Catalog Reads** | Per IP / Caller | 120 req / min | Allows product discovery and inventory checks without exhausting platform capacity. |
| **Agent Discovery & Planning** | Per Agent / Tenant | 60 req / min | Accommodates agent search, comparison, and evidence collection workflows. |
| **Mutating Financial Actions** | Per Tenant / Principal | 10 req / min | Strictly rate-limits checkout, payment intent creation, and mandate authorizations. |
| **Emergency Control Plane** | Per Operator | 30 req / min | Protects pause/resume routes from denial-of-service. |

---

## 2. Trigger Conditions & Detection Alarms

- **Surge in HTTP 429**: Rate limiting alarm fires when > 5% of incoming requests receive HTTP 429 `RATE_LIMIT_EXCEEDED`.
- **Runaway Agent Loop Alarm**: `LoopBudgetExceededError` or `AgentRunawayLoopError` raised when an agent executes > 5 tool calls or recursion depth > 3 without completing intent.
- **Brute-Force Authorization Spike**: Repeated rapid authorization submissions targeting high-value products.

---

## 3. Step-by-Step Triage & Mitigation Protocol

### Step 1: Differentiate Rogue Agent from External DoS

1. Inspect error logs to determine source of 429 responses:
   - If single `agent_id` triggering loops: proceed to Agent Loop Mitigation.
   - If distributed external IPs targeting `/api/v1/actions`: proceed to Edge WAF Throttling.

### Step 2: Agent Loop Mitigation

When an agent enters an infinite recursion or repetitive tool loop:
1. Identify the runaway agent:
   ```bash
   grep "LoopBudgetExceededError" /var/log/agentpay/api.log
   ```
2. Immediately suspend or quarantine the agent via `01-agent-revocation-runbook.md`.
3. Invalidate active loop trackers in `AgentLoopTracker`.

### Step 3: Edge Gateway Rate Limit Tightening

For external traffic bursts:
1. Apply dynamic IP / CIDR rate limit rule at the reverse proxy / Cloudflare edge.
2. Confirm the platform returns standardized HTTP 429 envelopes with `Retry-After`:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests. Please retry after 30 seconds.",
    "request_id": "req_8819a002",
    "trace_id": "trace_4412aa01",
    "details": {
      "retry_after_seconds": 30
    }
  }
}
```

---

## 4. Verification & Health Restoration

1. Monitor Prometheus metric `http_requests_total{status="429"}` to confirm error rates return below baseline (< 0.1%).
2. Verify that legitimate agent checkout traffic latency normalizes (< 150ms p95).

---

## 5. Audit Logging

High-severity rate limit violations and loop budget exhaustion events are recorded in the audit stream:
- **Event Type**: `AuditEventType.POLICY_EVALUATED` / `SECURITY_EVENT`
- **Data Payload**: Includes agent ID, tool call count, depth, and triggered threshold.

---

## 6. Cross-References
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md)
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md)

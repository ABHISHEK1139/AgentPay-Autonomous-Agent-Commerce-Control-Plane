# Rate Limit & Abuse Mitigation Runbook (§52, §62, §63, §64, §133)

> **Document Status**: Production Standard  
> **Runbook ID**: `RB-OPS-007`  
> **Severity / SLA**: Sev 2 (MTTA ≤ 15m, MTTC ≤ 30m, MTTR ≤ 2h)  
> **Service / Module**: `agentpay.api.rate_limiting`, `agentpay.domain.safety`, `agentpay.adapters.in_memory.rate_limiter`  
> **Author / Owner**: SRE Lead & Edge Gateway Team  
> **Version**: `1.0.0-production`  
> **Governing Specifications**: `AGENTS.md` (§52, §62, §63, §64, §133), Vector 07, OWASP API4  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Summary & Scope

AgentPay protects platform resources, financial ledgers, and merchant catalogs from denial-of-service, automated scraping, runaway agent execution cycles, and brute-force checkout spam. The platform implements multi-tiered, risk-adjusted rate limiting combined with agent execution loop budgets.

This runbook specifies:
- The risk-tiered rate limiting architecture distinguishing reads from mutating transactions.
- Detection alarms for HTTP 429 surges and agent loop budget exhaustion.
- Diagnostic procedures isolating external DoS attacks from rogue autonomous agent loops.
- Step-by-step containment actions including edge WAF rules, token bucket tuning, and agent quarantine.
- Verification protocols ensuring error rates and checkout latencies return to healthy baselines.

---

## 2. Risk-Tiered Rate Limiting Framework (§62, §64)

AgentPay divides incoming requests into 4 distinct risk tiers:

| Tier Name | Scope | Default Rate Limit | Burst Allowance | Target Purpose |
|---|---|---|---|---|
| **Public Catalog Reads** | Per IP / Caller | 120 req / min | 30 req | Allows product discovery and inventory browsing without backend saturation. |
| **Agent Discovery & Planning** | Per Agent / Tenant | 60 req / min | 15 req | Accommodates agent search, comparison, and evidence collection workflows. |
| **Mutating Financial Actions** | Per Tenant / Principal | 10 req / min | 2 req | Strictly rate-limits checkout, payment intent creation, and mandate authorizations. |
| **Emergency Control Plane** | Per Operator | 30 req / min | 5 req | Protects emergency pause and resume routes from denial-of-service exhaustion. |

### 2.1 Autonomous Agent Loop Budget Invariant (§64)
To prevent rogue LLM reasoning cycles from creating infinite tool-call loops:
- **Maximum Tool Calls**: An agent may execute at most **5 tool calls** per `ActionRequest`.
- **Maximum Recursion Depth**: An agent may nest planning or child action requests up to a maximum depth of **3**.
- Exceeding either boundary raises `LoopBudgetExceededError`, terminating the execution cycle without financial commitment.

---

## 3. Trigger Conditions & Detection Alarms

- **HTTP 429 Surge Alarm**: Fired when > 5% of incoming HTTP requests receive HTTP 429 `RATE_LIMIT_EXCEEDED` over a 5-minute rolling window.
- **Runaway Agent Loop Alarm**: Log alarm triggers on `LoopBudgetExceededError` or `AgentRunawayLoopError`.
- **Financial Endpoint Burst**: Mutating action endpoint `/api/v1/actions` receives sustained bursts > 20 req/sec from a single tenant partition.
- **Credential Brute-Force Alert**: Rapid 401/429 sequence targeting authentication endpoints.

---

## 4. Diagnostic Steps

### 4.1 Search Telemetry for Rate Limit Errors via PowerShell
```powershell
Get-Content -Path "C:\var\log\agentpay\api_access.log" -Tail 5000 | 
    Select-String -Pattern "429|RATE_LIMIT_EXCEEDED|LoopBudgetExceeded" | 
    Select-String -Pattern "client_ip=|agent_id="
```

### 4.2 Differentiate Rogue Agent from External Distributed DoS
1. **Analyze Offending Actor Distribution**:
   - If 429 errors concentrate on a single `agent_id` or `tenant_id`: Rogue agent loop.
   - If 429 errors originate from hundreds of distinct external IPs: Distributed volumetric attack / scraping attempt.
2. **Inspect Loop Tracker State in Telemetry**:
   ```powershell
   Select-String -Path "C:\var\log\agentpay\safety.log" -Pattern "LoopBudgetExceededError"
   ```

---

## 5. Mitigation & Recovery Playbook

### Step 1: Agent Runaway Loop Mitigation

When an agent enters an infinite recursion or recursive tool loop:
1. Identify the rogue agent ID from telemetry.
2. Transition the agent to `QUARANTINED` via PowerShell:
   ```powershell
   $headers = @{
       "Authorization" = "Bearer $env:OPERATOR_TOKEN"
       "X-Tenant-ID"   = "tenant_01"
       "Content-Type"  = "application/json"
   }
   $body = @{
       status = "QUARANTINED"
       reason = "Runaway tool loop budget exceeded (Vector 07)"
   } | ConvertTo-Json

   Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agents/ag_runaway_bot/status" `
       -Method Post -Headers $headers -Body $body
   ```
3. Purge active loop tracker state in `AgentLoopTracker`.

### Step 2: Edge Reverse Proxy / WAF Throttling

For external volumetric IP attacks:
1. Apply dynamic rate limiting or IP ban at the edge reverse proxy (Cloudflare / Nginx):
   ```bash
   # Example: Ban abusive CIDR block via Cloudflare API
   curl -s -X POST "https://api.cloudflare.com/client/v4/zones/{zone_id}/firewall/access_rules/rules" \
     -H "Authorization: Bearer <cf_token>" \
     -H "Content-Type: application/json" \
     -d '{
       "mode": "block",
       "configuration": {"target": "ip_range", "value": "198.51.100.0/24"},
       "notes": "Emergency edge block - rate limit abuse mitigation"
     }'
   ```

### Step 3: Verify Standard RFC 429 Error Envelopes

Confirm the API returns the standardized error format with `Retry-After` header:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests. Please retry after 30 seconds.",
    "request_id": "req_8819a002",
    "trace_id": "trace_4412aa01",
    "details": {
      "tier": "MUTATING_FINANCIAL",
      "retry_after_seconds": 30
    }
  }
}
```

---

## 6. Verification & Health Restoration

1. **Monitor Metric Normalization**:
   Confirm that Prometheus metric `rate(http_requests_total{status="429"}[5m])` drops back below 0.1%.
2. **Verify Financial Latency**:
   Verify that legitimate agent checkout latency normalizes to p95 < 150ms.
3. **Verify Read Route Health**:
   Confirm that `/api/v1/health` and `/agent/catalog` respond in < 25ms.

---

## 7. Audit Trail Accounting & Post-Mortem

Loop budget exhaustion and severe rate limiting events are committed to the audit ledger:
- **Event Type**: `AuditEventType.POLICY_EVALUATED` / `SECURITY_EVENT`
- **Data Payload**:
```json
{
  "event": "LOOP_BUDGET_EXCEEDED",
  "agent_id": "ag_runaway_bot",
  "tenant_id": "tenant_01",
  "tool_call_count": 6,
  "max_allowed_calls": 5,
  "recursion_depth": 3,
  "timestamp": "2026-10-10T12:00:00Z"
}
```

### 5-Whys Post-Mortem Template
1. **Why did the rate limit / loop alarm fire?** An autonomous agent generated 6 tool calls within a single action.
2. **Why did the agent loop?** The LLM model repeatedly invoked inventory checks without parsing the response format.
3. **Why did this not drain funds?** Invariant §64 enforced the hard limit of 5 tool calls and aborted before checkout.
4. **Why was tenant performance preserved?** Per-tenant token buckets prevented the loop from degrading other tenants.
5. **How will recurrence be prevented?** Update agent system prompt to provide structured error feedback on failed tool invocations.

---

## 8. Related References & Runbooks
- [01-agent-revocation-runbook.md](file:///docs/runbooks/01-agent-revocation-runbook.md) — Agent quarantine procedures.
- [02-emergency-kill-switch-runbook.md](file:///docs/runbooks/02-emergency-kill-switch-runbook.md) — Global kill switch operation.

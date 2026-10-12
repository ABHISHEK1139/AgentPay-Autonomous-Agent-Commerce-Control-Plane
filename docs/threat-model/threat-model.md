# AgentPay Threat Model & Security Architecture

> **Document Status**: Production Security Standard (Post-Remediation Revision)  
> **Governing Specifications**: `AGENTS.md` (§52–§65 Threat Model & Security Controls), `AgentPay_Implementation_Remediation_Report.md` (§4 Deliverable 7)  
> **Methodology**: STRIDE (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) + Agentic AI Threat Modeling  
> **Last Updated**: Remediation Sprint (2026-10-12)

---

## 1. Scope, Actors, and Trust Boundaries

AgentPay is the control plane between autonomous AI agents and financial execution rails. The system operates on a zero-trust model regarding external inputs and AI model outputs.

```text
       UNTRUSTED DOMAIN                      TRUST BOUNDARY                 DETERMINISTIC CONTROL PLANE
┌──────────────────────────────┐                                      ┌─────────────────────────────────┐
│ External AI Agents           │ ─── (ActionRequest Intent) ────────► │ API Authentication & Rate Limit │
│ Webhook Event Senders        │ ─── (Raw Signed Wire Bytes) ───────► │ HMAC-SHA256 Signature Verifier  │
│ Merchant Catalogs & Reviews  │ ─── (Product Claims / Prices) ─────► │ Evidence & Conflict Detector    │
│ External LLM / JEV Models    │ ─── (Advisory Risk Assessment) ───► │ Deterministic Policy Engine     │
└──────────────────────────────┘                                      └─────────────────────────────────┘
                                                                                      │
                                                                                      ▼
                                                                      ┌─────────────────────────────────┐
                                                                      │ Human-in-the-Loop Approvals     │
                                                                      │ (Exact 8-Field Bound Tokens)    │
                                                                      ├─────────────────────────────────┤
                                                                      │ Idempotent SAGA Orchestrator    │
                                                                      ├─────────────────────────────────┤
                                                                      │ SHA-256 Tamper-Evident Ledger   │
                                                                      └─────────────────────────────────┘
```

### Actors & Trust Classification

| Actor | Trust Level | Governing Boundary |
|---|---|---|
| **Human Principal / Approver** | Partially Trusted | Identity verified via cryptographically signed JWT; action approvals strictly bound to 8 fields. |
| **AI Agent** | **Untrusted** | Cannot modify own permissions, spend limits, or tool scopes. |
| **LLM / Decision Intelligence (JEV)** | **Untrusted Output** | Advisory only; cannot grant financial authority or override deterministic policies (§2, §18). |
| **Merchant / Catalog Provider** | **Untrusted Data Source** | External product descriptions, claims, and prices are treated as unverified claims, never instructions. |
| **Payment Provider (Razorpay/Stripe)** | Partially Trusted | Authoritative for payment state, but signatures and callbacks must be cryptographically verified. |
| **System Operator** | Trusted | Subject to multi-factor authentication, least privilege, and tamper-evident audit logging. |

---

## 2. Master Threat Register & Implemented Mitigations

### TM-01 — Floating-Point Monetary Arithmetic Drift
- **Threat (STRIDE: T/E)**: Precision loss in IEEE 754 floating-point operations causes budget rounding errors, enabling unauthorized spending ceiling breach.
- **Mitigation (§122)**: Complete eradication of binary floating-point numbers in domain models. All money is represented as integer minor units (`Money(minor_units=int, currency="INR")`) backed by ISO 4217 exponent tables.
- **Verification Evidence**: `apps/api/tests/unit/test_money.py`, `tests/adversarial/stress_test_money.py`.

### TM-02 — Webhook Account Mapping & Tenant Misattribution (AP-001)
- **Threat (STRIDE: S/E)**: Attackers forge webhooks without tenant identifiers, exploiting development defaults (`or "tenant_01"`) to inject payment confirmations into victim tenant ledgers.
- **Mitigation (AP-001)**: Zero fallback heuristics. Webhook tenant association is derived strictly from pre-persisted `provider_mappings` linking `(provider, provider_payment_id)` to the authentic `PaymentAttempt` and `tenant_id`. Unresolvable events are quarantined without state mutation.
- **Verification Evidence**: `tests/adversarial/stress_test_m1_tenant_isolation.py`, `apps/api/tests/unit/test_webhooks.py`.

### TM-03 — Webhook Wire-Byte Tampering & Format Confusion (AP-002)
- **Threat (STRIDE: T/S)**: Heuristic webhook handlers accepting canonicalized JSON or multiple payload encodings allow attackers to bypass signature validation by exploiting parser differentials.
- **Mitigation (AP-002)**: Strict, provider-specific raw-byte verification:
  - Razorpay: Raw body bytes signed via HMAC-SHA256 (`X-Razorpay-Signature`).
  - Stripe: Exact `<timestamp>.<raw_bytes>` signed via HMAC-SHA256 (`Stripe-Signature`) with ±300s freshness window.
  - Constant-time comparison (`hmac.compare_digest`). Re-serialized JSON fallbacks are prohibited in sandbox and production modes.
- **Verification Evidence**: `tests/adversarial/stress_test_webhook_crypto.py`.

### TM-04 — Webhook Replay After Application Restart (AP-003)
- **Threat (STRIDE: R/D)**: Payment webhooks replayed after server restart exploit process-local memory caches, causing double order confirmations or duplicate inventory releases.
- **Mitigation (AP-003)**: Durable database deduplication via `provider_webhook_events` table enforcing a unique database constraint on `(provider, provider_account_id, provider_event_id)`. Replays are atomically rejected across restarts and worker replicas.
- **Verification Evidence**: `tests/adversarial/stress_test_m2_durable_webhooks.py`.

### TM-05 — Approval Replay, Widening & Intra-Tenant Substitution (AP-006)
- **Threat (STRIDE: S/E)**: An agent reuses an approved token for a different purchase, or Agent B (acting for User Bob) consumes an approval granted to User Alice.
- **Mitigation (AP-006, ADR-0004)**: Cryptographic 8-field binding:
  `action_id`, `amount`, `currency`, `merchant_id`, `resource_id`, `action_version`, `principal_id`, and `tenant_id`.
  Approvals are single-use; `atomic_consume` executes an atomic database update with thread-safe locking and identity map expiration.
- **Verification Evidence**: `tests/adversarial/stress_test_m4_approval_binding.py`, `tests/adversarial/stress_test_m4_challenger_edge_cases.py`.

### TM-06 — Prompt Injection & Advisory AI Privilege Escalation (§25, §134, AP-011)
- **Threat (STRIDE: E)**: Adversarial text embedded in merchant product reviews or catalog descriptions instructs the AI model to ignore budget constraints or approve fraudulent purchases.
- **Mitigation (§2, §18, §25)**: AI models (LLMs, JEV) sit strictly below the authorization boundary. All external merchant text is treated as untrusted data. Model outputs provide advisory risk assessments only; deterministic policy engines evaluate hard spending limits and retain absolute decision authority.
- **Verification Evidence**: `apps/api/tests/unit/test_model_gateway.py`, `tests/adversarial/stress_test_model_gateway_resilience.py`.

### TM-07 — Provider Timeout Ambiguity & Double Charging (§43, AP-004, AP-017)
- **Threat (STRIDE: D/T)**: Network timeouts during payment capture cause the application to blindly retry, leading to double charges on buyer accounts.
- **Mitigation (§43, AP-017)**: Blind charge retries are prohibited. On timeout, `PaymentAttempt` transitions to `UNKNOWN`, triggering `PaymentReconciler` to perform a read-only authoritative query against the provider API. On confirmed failure, SAGA compensating transactions release reserved inventory.
- **Verification Evidence**: `tests/adversarial/stress_test_m8_sandbox_e2e_reconciliation.py`, `tests/adversarial/stress_test_m2_saga_concurrency.py`.

### TM-08 — Outbound SSRF & Cloud Metadata Exploitation (§60)
- **Threat (STRIDE: I/E)**: Malicious merchant URLs or agent webhook destinations cause the backend to query internal infrastructure (AWS/GCP metadata services, Redis, internal databases).
- **Mitigation (§60)**: Pre-flight outbound SSRF filtering validating HTTPS schemes, checking host allowlists, preventing DNS rebinding via IP resolution checks, and blocking loopback, RFC 1918, IMDS `169.254.169.254`, link-local, and IPv4-mapped IPv6 ranges.
- **Verification Evidence**: `apps/api/tests/unit/test_ssrf.py`, `tests/adversarial/stress_test_ssrf_resilience.py`.

### TM-09 — Privileged Audit Ledger Rewriting & Repudiation (§45, AP-015)
- **Threat (STRIDE: R/T)**: A compromised database administrator or application vulnerability modifies historical transaction logs to hide fraudulent actions.
- **Mitigation (§45)**: Tamper-evident SHA-256 cryptographic hash chaining (`current_hash = SHA-256(previous_hash + canonical_json(event))`). Preimage sequence validation detects any retroactively inserted, modified, or deleted records. Sensitive cardholder data and secrets are redacted prior to hashing.
- **Verification Evidence**: `apps/api/tests/unit/test_audit.py`, `scripts/verify_audit_chain.py --help`.

### TM-10 — Malicious Agent Recursion & Financial Exhaustion Loops (§64, §65)
- **Threat (STRIDE: D)**: A defective or compromised agent enters an infinite retry loop, submitting rapid microtransactions that exhaust daily budgets or overwhelm merchant APIs.
- **Mitigation (§64, §65)**: In-memory and persistent sliding-window rate limiters, agent loop tracking enforcing a maximum of 5 tool calls and recursion depth of 3, and cumulative daily/monthly financial spending limits.
- **Verification Evidence**: `apps/api/tests/unit/test_rate_limiter.py`, `apps/api/tests/unit/test_abuse_observability.py`.

### TM-11 — Multi-Instance Emergency Pause Desynchronization (AP-008, AP-010)
- **Threat (STRIDE: B)**: An operator triggers the emergency kill switch on node A, but nodes B and C continue processing transactions due to process-local pause state.
- **Mitigation (AP-008)**: Database-backed emergency pause records (`emergency_pause_records` table, Migration `0004`). All mutating application routes query shared authoritative pause state on every request, failing closed with HTTP 503 `EMERGENCY_PAUSE_ACTIVE`.
- **Verification Evidence**: `tests/adversarial/stress_test_m6_shared_operational_controls.py`.

---

## 3. Residual Risks & Future Hardening

1. **PostgreSQL Clustering in Production**: SQLite with `StaticPool` was verified under multi-threaded concurrency. Multi-node PostgreSQL deployments require connection-level row locking (`SELECT ... FOR UPDATE`) in high-throughput clusters.
2. **External Checkpoint Notarization**: While the SHA-256 ledger is cryptographically chained, external timestamping (e.g. RFC 3161 or public ledger anchoring) will provide non-repudiation against an adversary with full database replacement privileges.
3. **Hardware Security Modules (HSM)**: Production deployment will transition webhook secrets and JWT signing keys to cloud KMS / HSM storage.

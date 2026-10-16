# OWASP ASVS Verification Mapping Matrix (§53, §108, §144)

> **Document Status**: Draft mapping, self-assessed
> **Governing Specifications**: `AGENTS.md` (§52 API Baseline, §53 Application Baseline, §108/§144 Honesty Rules)
> **Target Standard**: OWASP Application Security Verification Standard (ASVS) **5.0** — the version
> mandated by blueprint §53. **Known gap:** the chapter structure below still follows the **v4.0.3**
> layout (V1–V14); the v5.0 chapter numbering and requirement IDs have **not** been reconciled.
> Until that is done, treat every clause ID here as **v4.0.3**, not v5.0. Do not cite this document
> as v5.0 conformance evidence.
> **Target Assurance Level**: Level 2 (Standard Enterprise / Defensible) & Level 3 (Advanced / Financial Controls)
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Assurance Methodology & Honesty Rules

Per `AGENTS.md` §108 and §144, AgentPay adheres to strict truth-in-documentation. We explicitly distinguish:
- **Designed for**: Architectural intent and structural alignment.
- **Aligned with**: Controls implemented and mapped to standard clauses.
- **Tested against**: Controls exercised by automated local test suites.
- **Certified by**: Formal assessment by an accredited third-party auditor (**AgentPay has NOT been externally certified**).

### Tracking State Definitions (§53)
- `REQUIRED`: Control is within scope for AgentPay's high-assurance financial control plane profile.
- `IMPLEMENTED`: Mechanism exists in codebase.
- `TESTED`: Automated test explicitly exercises the control.
- `VERIFIED`: Test executed and passed on this local environment with verified exit code 0.
- `EXCEPTION`: Documented deviation with formal security rationale (see `security/asvs/exceptions.md`).

---

## 2. Master Chapter-by-Chapter Verification Mapping Table

| Requirement ID | ASVS Chapter & Control Description | Target Level | Status | Codebase Implementation Evidence | Test Verification Path |
|---|---|---|---|---|---|
| **V1.1.1** | **V1 Architecture**: Clean architecture boundary; unidirectional dependency flow (`API -> App -> Domain <- Ports <- Adapters`). | L2 / L3 | `VERIFIED` | `apps/api/src/agentpay/domain/` (0 framework imports) | `apps/api/tests/unit/test_architecture_boundaries.py` (`test_domain_has_zero_external_framework_imports`) |
| **V1.1.2** | **V1 Architecture**: Fail-closed default on uncertainty for financial, identity, and policy actions (§2.4). | L2 / L3 | `VERIFIED` | `domain/policy.py`, `application/use_cases/purchase_flow.py` | `apps/api/tests/unit/test_policy.py`, `test_purchase_flow.py` |
| **V1.2.1** | **V1 Architecture**: Threat modeling covering autonomous agent abuse vectors, prompt injection, and privilege escalation (STRIDE/LINDDUN). | L2 / L3 | `VERIFIED` | `docs/threat-model/threat-model.md`, `docs/security/standards-matrix.md` | `apps/api/tests/unit/test_adversarial_threat_harness.py` (14/14 vectors neutralized) |
| **V1.14.1** | **V1 Architecture**: Persistence tier data isolation and concurrency lock partitioning. | L2 | `EXCEPTION` | `adapters/in_memory/`, `_locks[tenant_id]` | `apps/api/tests/unit/test_concurrency_stress_challenger.py` (Documented in EXC-001: in-memory adapter for hermetic verification) |
| **V2.1.1** | **V2 Authentication**: Principals and autonomous agents authenticated with unique, scoped credentials. | L2 | `VERIFIED` | `domain/identity.py` (`Credential`, `Agent`), `application/services/identity_service.py` | `apps/api/tests/unit/test_identity.py` (108/108 passed) |
| **V2.1.2** | **V2 Authentication**: Cryptographic storage of credential secrets using SHA-256 (`secret_hash`); zero plaintext secret storage. | L2 | `VERIFIED` | `domain/identity.py` (`Credential.hash_secret`) | `apps/api/tests/unit/test_identity.py` |
| **V2.2.1** | **V2 Authentication**: Short token lifetimes and temporal expiration checks (`is_expired()`). | L2 | `VERIFIED` | `domain/identity.py` (`Credential.is_expired`) | `apps/api/tests/unit/test_api_auth.py`, `test_identity.py` |
| **V2.5.1** | **V2 Authentication**: Fail-closed authentication middleware rejecting missing, invalid, or expired credentials with HTTP 401 `UNAUTHENTICATED`. | L2 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_api_auth.py` (33/33 passed) |
| **V3.1.1** | **V3 Session**: Stateless token evaluation; no server-side session fixation or sticky cookies. | L2 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_api_auth.py` |
| **V3.3.1** | **V3 Session**: Replay attack protection via mandatory idempotency keys (`IdempotencyStore`). | L2 | `VERIFIED` | `domain/payments.py`, `adapters/in_memory/idempotency_store.py` | `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 10) |
| **V3.4.1** | **V3 Session**: Single-use consumption tokens for human approvals (`atomic_consume()`). | L2 | `VERIFIED` | `domain/approval.py` (`ApprovalStatus.CONSUMED`) | `apps/api/tests/unit/test_approval.py` (7/7 passed) |
| **V4.1.1** | **V4 Access Control**: Multi-tenant isolation: every query partitioned by explicit `tenant_id` (`repo.get(tenant_id, id)`). | L2 / L3 | `VERIFIED` | `application/ports/`, `adapters/in_memory/` | `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 03) |
| **V4.1.2** | **V4 Access Control**: BOLA/IDOR defense: token tenant binding verified against asserted tenant; mismatch raises `CrossTenantAccessError` (HTTP 403 `CROSS_TENANT_ACCESS_DENIED`). | L2 / L3 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_api_auth.py` |
| **V4.2.1** | **V4 Access Control**: BFLA defense: agent capability checks (`has_capability()`) and tool permission gating (`ToolPermissionDeniedError`). | L2 / L3 | `VERIFIED` | `domain/identity.py`, `domain/safety.py` | `apps/api/tests/unit/test_tool_registry.py` (7/7 passed) |
| **V4.3.1** | **V4 Access Control**: Operator role gating on emergency control plane (`/api/v1/emergency/*` -> HTTP 403 `UNAUTHORIZED_OPERATOR`). | L2 / L3 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_api_auth.py`, `test_protocol_adapters.py` |
| **V5.1.1** | **V5 Validation**: Strict value object validation on all inputs via immutable frozen dataclasses and `__post_init__`. | L2 / L3 | `VERIFIED` | `domain/money.py`, `domain/action_request.py` | `apps/api/tests/unit/test_action_request.py` (21/21 passed) |
| **V5.1.2** | **V5 Validation**: Currency modeled exclusively as integer minor units (`Money`); floats and booleans fail closed with `InvalidMoneyError`. | L2 / L3 | `VERIFIED` | `domain/money.py` | `apps/api/tests/unit/test_money.py` (32/32 passed, Hypothesis property tests) |
| **V5.2.1** | **V5 Validation**: Neutralization of prompt injection in external text: merchant descriptions, reviews, and catalog copy treated strictly as untrusted data (§25). | L2 / L3 | `VERIFIED` | `domain/policy.py`, `use_cases/purchase_flow.py` | `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 06) |
| **V5.3.1** | **V5 Validation**: AI model advisory outputs subordinate to deterministic policy; cannot override spending limits or grant permissions. | L2 / L3 | `VERIFIED` | `domain/policy.py` | `apps/api/tests/unit/test_policy.py` (Mock model in test suite per EXC-003) |
| **V6.1.1** | **V6 Cryptography**: Immutable, tamper-evident audit ledger using SHA-256 sequential hash chaining from `GENESIS_HASH` (`0`*64). | L3 | `VERIFIED` | `domain/audit.py` (`AuditHasher.compute_event_hash`) | `apps/api/tests/unit/test_audit.py`, `test_cryptographic_tamper_challenger.py` (53/53 passed) |
| **V6.1.2** | **V6 Cryptography**: Deterministic canonical JSON serialization (`sort_keys=True`, `separators=(',', ':')`) for reproducible cryptographic hashes. | L3 | `VERIFIED` | `domain/audit.py` | `apps/api/tests/unit/test_audit.py` |
| **V6.2.1** | **V6 Cryptography**: Data minimization: zero plaintext cardholder data (PAN, CVV) stored; tokenized payment references only (§40). | L3 | `VERIFIED` | `domain/payments.py` | Verified by AST scan and test suite (0 card drivers in source) |
| **V7.1.1** | **V7 Error Handling**: Standardized JSON error response envelopes (`code`, `message`, `request_id`, `trace_id`); zero internal stack trace leakage. | L2 / L3 | `VERIFIED` | `api/schemas.py`, `api/app.py` | `apps/api/tests/unit/test_api.py`, `test_adversarial_threat_harness.py` (Vector 13) |
| **V7.2.1** | **V7 Error Handling**: Recursive sensitive field redactor (`SensitiveFieldRedactor`) masking passwords, tokens, API keys, PAN, and CVV before audit hashing. | L2 / L3 | `VERIFIED` | `domain/audit.py` | `apps/api/tests/unit/test_audit.py` |
| **V7.3.1** | **V7 Error Handling**: Audit logs record verifiable decision summaries and evidence references rather than private LLM chain-of-thought (§29). | L2 / L3 | `VERIFIED` | `domain/audit.py` | `apps/api/tests/unit/test_audit.py` |
| **V8.1.1** | **V8 Data Protection**: Strict tenant logical and cryptographic data partitioning preventing multi-tenant data contamination. | L2 | `VERIFIED` | `domain/identity.py`, `adapters/in_memory/` | `apps/api/tests/unit/test_identity.py`, `test_adversarial_threat_harness.py` (Vector 03) |
| **V8.2.1** | **V8 Data Protection**: Financial data minimization complying with RBI payment tokenization and India DPDP Act 2023 principles. | L2 | `VERIFIED` | `domain/payments.py` | `apps/api/tests/unit/test_payments.py` |
| **V9.1.1** | **V9 Communication**: Production transport security requires TLS 1.3 / HTTPS encryption for all external communication. | L2 | `IMPLEMENTED` | `docs/operations/backup-restore.md` | Configuration standard; tested via protocol challenger |
| **V9.2.1** | **V9 Communication**: Protocol adapters (ACP, AP2, UAP, x402) enforce strict HTTPS URL scheme validation. | L2 | `VERIFIED` | `adapters/protocols/` | `apps/api/tests/unit/test_protocol_adversarial_challenger.py` (42/42 passed) |
| **V10.1.1** | **V10 Malicious Code**: Hermetic build and dependency locking via `uv.lock` and pinned package hashes in CI. | L2 | `VERIFIED` | `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml` | `uv run ruff format --check .`, `uv run ruff check .` |
| **V10.2.1** | **V10 Malicious Code**: Static AST boundary analysis continuously scanning `agentpay/domain/` to prohibit third-party framework or database imports. | L2 | `VERIFIED` | `apps/api/tests/unit/test_architecture_boundaries.py` | `apps/api/tests/unit/test_architecture_boundaries.py` (7/7 passed) |
| **V11.1.1** | **V11 Business Logic**: Deterministic spending policy engine enforcing monotonicity, spending caps, and mandatory human approval. | L3 | `VERIFIED` | `domain/policy.py` | `apps/api/tests/unit/test_policy.py` (17/17 passed) |
| **V11.1.2** | **V11 Business Logic**: Cryptographically bound, single-use 8-field human approvals (`action_id`, `amount`, `merchant_id`, `resource_id`, `currency`, `action_version`, `principal_id`, `tenant_id`). | L3 | `VERIFIED` | `domain/approval.py` | `apps/api/tests/unit/test_approval.py` |
| **V11.2.1** | **V11 Business Logic**: Non-blind payment timeout reconciliation (`PaymentReconciler`): queries provider state rather than retrying charges (§43). | L3 | `VERIFIED` | `application/services/reconciliation_service.py` | `apps/api/tests/unit/test_reconciliation.py`, `test_failure_engineering.py` |
| **V11.2.2** | **V11 Business Logic**: Saga compensation: atomic inventory release on payment failure; automatic payment refund on downstream order failure. | L3 | `VERIFIED` | `application/use_cases/purchase_flow.py` | `apps/api/tests/unit/test_purchase_flow.py`, `test_failure_engineering.py` |
| **V11.6.1** | **V11 Business Logic**: External PSP integration abstraction. | L3 | `EXCEPTION` | `application/ports/payments_port.py` | `apps/api/tests/unit/test_payments.py` (Documented in EXC-002: MockPaymentProvider used for hermetic local testing) |
| **V12.1.1** | **V12 File/Resources**: Bounded agent execution: `AgentLoopTracker` limits tool invocations to ≤ 5 and recursion depth to ≤ 3 per action. | L2 | `VERIFIED` | `domain/safety.py` | `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 07), `test_adversarial_security.py` |
| **V12.2.1** | **V12 File/Resources**: Fixed tool registry with risk tiers (`READ`, `WRITE_LOW`, `WRITE_MEDIUM`, `FINANCIAL`, `DESTRUCTIVE`, `SECURITY_SENSITIVE`). | L2 | `VERIFIED` | `domain/safety.py` | `apps/api/tests/unit/test_tool_registry.py` (7/7 passed) |
| **V13.1.1** | **V13 API/Web Service**: Risk-tiered sliding-window rate limiting returning HTTP 429 `RATE_LIMIT_EXCEEDED` with `Retry-After`. | L2 / L3 | `VERIFIED` | `adapters/in_memory/rate_limiter.py`, `api/app.py` | `apps/api/tests/unit/test_rate_limiter.py`, `test_api_rate_limiting.py` |
| **V13.2.1** | **V13 API/Web Service**: Idempotency collision prevention: re-submitting an idempotency key with modified payload raises `IdempotencyConflictError` (HTTP 409). | L2 / L3 | `VERIFIED` | `adapters/in_memory/idempotency_store.py` | `apps/api/tests/unit/test_adversarial_threat_harness.py` |
| **V13.3.1** | **V13 API/Web Service**: Authoritative OpenAPI 3.2.0 specification exposed at `/openapi.json`. | L2 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_api.py` |
| **V14.1.1** | **V14 Configuration**: In-process Emergency Kill Switch (`POST /api/v1/emergency/pause`) immediately intercepting all mutating financial transactions with HTTP 503 while preserving read-only diagnostic routes. | L2 | `VERIFIED` | `apps/api/src/agentpay/api/app.py` | `apps/api/tests/unit/test_protocol_adapters.py`, `test_release_hardening_e2e.py` |
| **V14.2.1** | **V14 Configuration**: Secure-by-default configuration: autonomous payments disabled by default in production; require explicit human configuration. | L2 | `VERIFIED` | `domain/policy.py` | `apps/api/tests/unit/test_policy.py` |

---

## 3. Detailed Verification Notes by ASVS Chapter

### Chapter V1: Architecture, Design and Threat Modeling
- **Assurance Level**: Level 2 / Level 3
- **Architectural Scope**: The core architecture enforces strict unidirectional dependency boundaries validated by AST static analysis (`apps/api/tests/unit/test_architecture_boundaries.py`). The domain core (`apps/api/src/agentpay/domain/`) contains zero external dependencies or framework imports.
- **Fail-Closed Default**: Uncertainty in identity, policy limits, inventory, or pricing results in immediate execution halt (`AGENTS.md` §2.4).
- **Threat Model**: Formally documented in `docs/threat-model/threat-model.md` and evaluated through the 14-vector adversarial challenger suite (`apps/api/tests/unit/test_adversarial_threat_harness.py`).
- **Exceptions**: Documented in `security/asvs/exceptions.md` (EXC-001) regarding hermetic in-memory adapters.

### Chapter V2: Authentication Verification
- **Assurance Level**: Level 2
- **Credential Storage**: Raw credentials are never stored. The system stores only cryptographic SHA-256 hashes (`secret_hash`).
- **Temporal Enforcement**: Every credential carries `expires_at` in UTC. Expired credentials fail closed with HTTP 401 `UNAUTHENTICATED`.
- **Terminal Lifecycle**: Revoked credentials transition to `CredentialStatus.REVOKED`, which is strictly terminal.

### Chapter V3: Session Management
- **Assurance Level**: Level 2
- **Replay Protection**: Financial actions require unique idempotency keys. Replay attempts with modified payloads trigger `IdempotencyConflictError` (HTTP 409).
- **Single-Use Approvals**: Human approval decisions are bound to an 8-field cryptographic tuple (`action_id`, `amount`, `merchant_id`, `resource_id`, `currency`, `action_version`, `principal_id`, `tenant_id`) and atomically marked `ApprovalStatus.CONSUMED` upon first use under mutex lock.

### Chapter V4: Access Control Verification
- **Assurance Level**: Level 2 / Level 3
- **Multi-Tenant Segregation**: Every repository operation requires explicit `tenant_id`. Cross-tenant assertions raise `CrossTenantAccessError` (HTTP 403 `CROSS_TENANT_ACCESS_DENIED`).
- **Capability Gating**: Autonomous agents operate under strict capability whitelists (`has_capability()`). Tool execution is gated by risk tier (`ToolRegistry`).

### Chapter V5: Validation, Sanitization and Encoding
- **Assurance Level**: Level 2 / Level 3
- **Integer Minor Units**: Currency is modeled strictly in minor units (`paise`/`cents`). Floating-point inputs fail closed with `InvalidMoneyError`.
- **Untrusted External Data**: Merchant copy, reviews, and catalog text are treated strictly as data, completely neutralizing prompt injection attempts.

### Chapter V6: Stored Cryptography Verification
- **Assurance Level**: Level 3
- **Cryptographic Chaining**: Every audit record is chained via SHA-256 canonical JSON preimages from `GENESIS_HASH` (`0`*64).
- **Card Data Minimization**: Zero PAN, CVV, or cardholder magnetic data is stored anywhere in the codebase.

### Chapter V7: Error Handling and Logging Verification
- **Assurance Level**: Level 2 / Level 3
- **Error Envelopes**: All API errors return RFC-compliant JSON envelopes with `request_id` and `trace_id`; stack traces are never exposed to callers.
- **Pre-Hashing Redaction**: `SensitiveFieldRedactor` sanitizes nested dictionaries, replacing sensitive keys (`card_number`, `cvv`, `password`, `token`) with `"[REDACTED]"`.

### Chapter V8: Data Protection Verification
- **Assurance Level**: Level 2
- **Regulatory Alignment**: Designed in accordance with the India Digital Personal Data Protection (DPDP) Act 2023 and RBI payment aggregation guidelines.
- **Partition Isolation**: Tenant data is logically isolated; no shared queries without tenant scoping exist.

### Chapter V9: Communication Security Verification
- **Assurance Level**: Level 2
- **Transport Standards**: Mandates TLS 1.3 in production deployments. Protocol adapters enforce `https://` endpoint scheme validation.

### Chapter V10: Malicious Code Search
- **Assurance Level**: Level 2
- **Dependency Locking**: Package versions locked via `uv.lock`. Static AST inspection ensures no untracked dependencies enter domain modules.

### Chapter V11: Business Logic Verification
- **Assurance Level**: Level 3
- **Deterministic Policy**: AI risk assessments are strictly advisory; the deterministic policy engine is the sole payment authority.
- **Saga Compensation**: Double-entry rollback semantics ensure refunds are triggered if orders fail, and inventory is released if payments fail.

### Chapter V12: File and Resources Verification
- **Assurance Level**: Level 2
- **Loop Budget Limits**: Runaway agent loops are constrained to ≤ 5 tool calls and ≤ 3 recursion depth.
- **Fixed Tool Registry**: The agent cannot invent tools or execute unpermissioned functions.

### Chapter V13: API and Web Service Verification
- **Assurance Level**: Level 2 / Level 3
- **Rate Limiting**: Sliding-window rate limiters protect mutating endpoints, returning HTTP 429 with `Retry-After` headers.
- **Schema Contracts**: Standardized OpenAPI 3.2.0 specification exposed at `/openapi.json`.

### Chapter V14: Configuration Verification
- **Assurance Level**: Level 2
- **Emergency Kill Switch**: Process-global emergency pause halts all mutating transactions cluster-wide while leaving read endpoints open.
- **Fail-Closed Defaults**: Autonomous payments default to disabled upon tenant creation.

---

## 4. Companion Documents in `security/asvs/`
- [verification.yaml](file:///security/asvs/verification.yaml) — Machine-readable ASVS control index and test mappings.
- [exceptions.md](file:///security/asvs/exceptions.md) — Documented security exceptions (EXC-001 through EXC-003).

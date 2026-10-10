# OWASP ASVS v4.0 Verification Mapping Matrix (§53, §108, §144)

> **Document Status**: Production Security Baseline  
> **Governing Specifications**: `AGENTS.md` (§52 API Baseline, §53 Application Baseline, §108/§144 Honesty Rules)  
> **Target Standard**: OWASP Application Security Verification Standard (ASVS) v4.0.3  
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
- `EXCEPTION`: Documented deviation with formal security rationale (see `exceptions.md`).

---

## 2. Chapter-by-Chapter Master Mapping Matrix (V1 through V14)

### V1: Architecture, Design and Threat Modeling
- **Target Level**: L2 / L3
- **Objective**: Verify that the application architecture enforces clean separation of concerns, fail-closed defaults, and comprehensive threat modeling.
- **Codebase Implementation**:
  - Modular monolith architecture with strict unidirectional dependency flow (`API -> Application -> Domain <- Ports <- Adapters`).
  - Pure domain core with 0 external I/O or framework imports (`apps/api/src/agentpay/domain/`).
  - Comprehensive threat models (`docs/threat-model/threat-model.md`, `docs/security/standards-matrix.md`).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_architecture_boundaries.py` (7/7 passed, AST-enforced dependency direction).
  - `apps/api/tests/unit/test_adversarial_threat_harness.py` (14/14 vectors neutralized).
- **Status**: `VERIFIED`

---

### V2: Authentication Verification
- **Target Level**: L2
- **Objective**: Verify that credentials represent identity securely, prevent impersonation, and resist credential stuffing.
- **Codebase Implementation**:
  - Scoped `Credential` domain model bound to `tenant_id` and `principal_id` (`domain/identity.py`).
  - SHA-256 hashed secret storage (`secret_hash`), zero plaintext credentials stored.
  - Expiration tracking (`is_expired()`) and terminal status lifecycle (`CredentialStatus.REVOKED`).
  - HTTP middleware rejecting unauthenticated requests with HTTP 401 `UNAUTHENTICATED`.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_api_auth.py` (33/33 passed).
  - `apps/api/tests/unit/test_identity.py` (108/108 passed).
- **Status**: `VERIFIED`

---

### V3: Session Management
- **Target Level**: L2
- **Objective**: Verify that user and agent session tokens resist replay, fixation, and unauthorized extension.
- **Codebase Implementation**:
  - Stateless token evaluation with cryptographic verification.
  - Idempotency key store (`IdempotencyStore`) preventing payment duplicate submissions.
  - Single-use human approval binding consumption (`atomic_consume()`) under mutex lock.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_approval.py` (7/7 passed).
  - `apps/api/tests/unit/test_concurrency_stress_challenger.py` (Vector 10 approval replay blocked).
- **Status**: `VERIFIED`

---

### V4: Access Control Verification
- **Target Level**: L2 / L3
- **Objective**: Verify that authorization is enforced on every request, preventing Broken Object Level Authorization (BOLA/IDOR) and Broken Function Level Authorization (BFLA).
- **Codebase Implementation**:
  - Multi-tenant data partitioning on every repository method (`repo.get(tenant_id, id)`).
  - Token tenant binding check (`Asserted tenant != Credential tenant -> 403 CROSS_TENANT_ACCESS_DENIED`).
  - Fixed `ToolRegistry` with capability gating (`ToolPermissionDeniedError`).
  - Operator role gating on emergency kill switch (`/api/v1/emergency/* -> 403 UNAUTHORIZED_OPERATOR`).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_api_auth.py` (cross-tenant rejection tests).
  - `apps/api/tests/unit/test_tool_registry.py` (7/7 passed).
  - `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vectors 01, 03).
- **Status**: `VERIFIED`

---

### V5: Validation, Sanitization and Encoding
- **Target Level**: L2 / L3
- **Objective**: Verify that all incoming external data is validated against strict schemas before processing.
- **Codebase Implementation**:
  - Immutable frozen dataclasses with `__post_init__` validation across all domain objects.
  - Currency strictly modeled as integer minor units (`Money`); floats and booleans fail closed with `InvalidMoneyError`.
  - Untrusted external text (merchant descriptions, reviews) treated strictly as data, neutralizing prompt injection (§25).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_money.py` (32/32 passed, Hypothesis property tests).
  - `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 06 prompt injection neutralized).
- **Status**: `VERIFIED`

---

### V6: Stored Cryptography Verification
- **Target Level**: L3
- **Objective**: Verify that cryptographic algorithms and preimages guarantee data integrity and confidentiality.
- **Codebase Implementation**:
  - SHA-256 hash-chained immutable audit ledger (`AuditHasher.compute_event_hash`).
  - Canonical JSON determinism (`json.dumps(..., sort_keys=True, separators=(',', ':'))`).
  - Genesis hash linkage (`0` * 64) and sequential continuity.
  - Zero storage of card numbers, CVVs, or bank credentials (§40).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_cryptographic_tamper_challenger.py` (53/53 passed).
  - `scripts/verify_audit_chain.py` (tamper detection verified).
- **Status**: `VERIFIED`

---

### V7: Error Handling and Logging Verification
- **Target Level**: L2 / L3
- **Objective**: Verify that errors never disclose sensitive information, stack traces are withheld, and audit logs are sanitized.
- **Codebase Implementation**:
  - Standardized JSON error envelopes: `{"error": {"code": "...", "message": "...", "request_id": "...", "trace_id": "..."}}`.
  - Recursive sensitive field redactor (`SensitiveFieldRedactor`) masking passwords, tokens, PAN, CVV as `"[REDACTED]"`.
  - Decision summaries recorded in audit logs rather than private chain-of-thought (§29).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_audit.py` (redaction tests passed).
  - `apps/api/tests/unit/test_api.py` (error envelope structure verified).
- **Status**: `VERIFIED`

---

### V8: Data Protection Verification
- **Target Level**: L2
- **Objective**: Verify data minimization, tenant privacy, and alignment with data protection regulations (India DPDP Act / GDPR).
- **Codebase Implementation**:
  - Strict tenant partition boundaries preventing cross-tenant leakage.
  - Payment data minimization: tokenized references only, no cardholder data environment (CDE) in scope.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_identity.py` (tenant partitioning verified).
  - Import scan confirming zero database drivers or card SDKs in source.
- **Status**: `VERIFIED`

---

### V9: Communication Security Verification
- **Target Level**: L2
- **Objective**: Verify that all communications enforce modern TLS and secure HTTP transport.
- **Codebase Implementation**:
  - HTTPS / TLS 1.3 mandated in production deployment configurations.
  - Protocol adapters enforce strict URL scheme validation (`https://`).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_protocol_adversarial_challenger.py` (42/42 passed).
- **Status**: `IMPLEMENTED` / `TESTED`

---

### V10: Malicious Code Search
- **Target Level**: L2
- **Objective**: Verify that code dependencies are locked and supply chain risks are minimized.
- **Codebase Implementation**:
  - Hermetic dependency management via `uv.lock`.
  - AST boundary test (`test_architecture_boundaries.py`) continuously scanning for unapproved third-party imports.
- **Test Verification Evidence**:
  - `uv run ruff check .` (clean).
  - `apps/api/tests/unit/test_architecture_boundaries.py` (7/7 passed).
- **Status**: `VERIFIED`

---

### V11: Business Logic Verification
- **Target Level**: L3
- **Objective**: Verify that business logic enforces deterministic spending policies, human approval thresholds, and atomic inventory reservations.
- **Codebase Implementation**:
  - Deterministic spending policy engine with 4 discrete decisions (`ALLOW`, `DENY`, `REQUIRE_APPROVAL`, `FLAG`).
  - Immutable 7-field approval binding (`action_id`, `amount`, `merchant_id`, `resource_id`, `currency`, `action_version`, `tenant_id`).
  - Saga compensation pattern: dual-direction rollback (refund payment on order failure; release inventory on payment failure).
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_policy.py` (17/17 passed, monotonicity property verified).
  - `apps/api/tests/unit/test_approval.py` (binding verification passed).
  - `apps/api/tests/unit/test_purchase_flow.py` (saga compensation verified).
- **Status**: `VERIFIED`

---

### V12: File and Resources Verification
- **Target Level**: L2
- **Objective**: Verify that resource consumption is bounded, preventing denial-of-service and runaway loops.
- **Codebase Implementation**:
  - Fixed `ToolRegistry` with risk tiers (`READ`, `WRITE_LOW`, `WRITE_MEDIUM`, `FINANCIAL`, `DESTRUCTIVE`, `SECURITY_SENSITIVE`).
  - `AgentLoopTracker` bounding tool executions to a maximum of 5 invocations and recursion depth to 3.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_tool_registry.py` (7/7 passed).
  - `apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 07 loop protection verified).
- **Status**: `VERIFIED`

---

### V13: API and Web Service Verification
- **Target Level**: L2 / L3
- **Objective**: Verify that APIs enforce rate limiting, idempotency conflict prevention, and standardized schema validation.
- **Codebase Implementation**:
  - Sliding-window defensive rate limiter (`InMemoryRateLimiter`) returning HTTP 429 `RATE_LIMIT_EXCEEDED`.
  - Idempotency store detecting payload modifications under reused keys (`IdempotencyConflictError`).
  - Complete OpenAPI 3.1.0 specification exposed at `/openapi.json`.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_api.py` (OpenAPI and error schemas verified).
  - `apps/api/tests/unit/test_adversarial_threat_harness.py` (idempotency conflict tests).
- **Status**: `VERIFIED`

---

### V14: Configuration Verification
- **Target Level**: L2
- **Objective**: Verify that default configurations are secure, fail-closed, and support immediate emergency mitigation.
- **Codebase Implementation**:
  - Emergency Kill Switch (`/api/v1/emergency/pause`) immediately halting all mutating autonomous payments with HTTP 503.
  - Fail-closed defaults when configuration or policy parameters are absent.
- **Test Verification Evidence**:
  - `apps/api/tests/unit/test_protocol_adapters.py` (kill switch probe).
  - `apps/api/tests/unit/test_release_hardening_e2e.py` (4/4 passed).
- **Status**: `VERIFIED`

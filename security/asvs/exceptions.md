# OWASP ASVS v4.0 Formal Security Exceptions (§53, §108, §144)

> **Document Status**: Production Security Standard  
> **Governing Specifications**: `AGENTS.md` (§53 Application Baseline, §108/§144 Honesty Rules)  
> **Last Verified**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Exception Governance Policy

In accordance with `AGENTS.md` §53 and §144:
- Every security control defined in ASVS Level 2/3 that is not fully verified in this repository must have a formal, documented exception.
- No exception may be approved to bypass failing tests or weaken invariants.
- Exceptions record the governing rationale, compensating controls, and planned resolution milestone.

---

## 2. Documented Exceptions

### EXC-001: In-Memory Adapters for Hermetic Local Verification
- **ASVS Control**: V1.14 (Persistence & Database Hardening)
- **Status**: `EXCEPTION` / `BY DESIGN`
- **Scope**: Current repository test harness utilizes thread-safe, lock-guarded in-memory repository adapters rather than live PostgreSQL/Redis network daemons.
- **Security Rationale**: Zero-dependency local gate execution allows deterministic unit and adversarial test execution without flaky network ports or external daemon lifecycles.
- **Compensating Controls**:
  - Exact repository interfaces defined via `@runtime_checkable Protocol` (`identity_port.py`, `commerce_port.py`, `approval_port.py`, `audit_port.py`).
  - Cross-tenant access denial (`CrossTenantAccessError`) and partition locks (`_locks[tenant_id]`) strictly verified in memory.
  - SQL schema definitions and migrations declared in `infrastructure/database/` for production deployment.

---

### EXC-002: In-Process Mock Payment Provider
- **ASVS Control**: V11.6 (External Financial Integration)
- **Status**: `EXCEPTION` / `BY DESIGN`
- **Scope**: Core test suites execute against `MockPaymentProvider` rather than live PSP endpoints (Razorpay/Stripe).
- **Security Rationale**: Tests must not incur real financial charges or require live internet credentials during automated CI gate execution.
- **Compensating Controls**:
  - `PaymentProvider` protocol requires explicit idempotency keys and structured status returns.
  - Flaky network simulations, gateway timeouts, duplicate charges, and saga refund compensations are thoroughly exercised in `test_failure_engineering.py`.

---

### EXC-003: In-Process Model Decision Engine (Mock)
- **ASVS Control**: V5.1 (AI Input Validation & Model Gating)
- **Status**: `EXCEPTION` / `BY DESIGN`
- **Scope**: Tests utilize `MockDecisionEngine` instead of live LLM or JEV cloud API calls.
- **Security Rationale**: Deterministic test suites must execute in < 15 seconds without rate limits, API bills, or non-deterministic hallucinations.
- **Compensating Controls**:
  - Advisory AI outputs are strictly subordinate to the deterministic policy engine (`AGENTS.md` §2.2).
  - Malicious prompt injection strings are tested directly against policy boundaries (`test_adversarial_threat_harness.py`, Vector 05/06).

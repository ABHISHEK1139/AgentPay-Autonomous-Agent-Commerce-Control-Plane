# Standards Matrix

> **Status**: Verified Security & Standards Mapping (Updated for Milestone 3 / Requirement R3)  
> **Authoritative Evaluation Report**: [`docs/security/industrial-standards-compliance-evaluation-report.md`](industrial-standards-compliance-evaluation-report.md)  
> **Last Updated**: 2026-11-18  

## Honesty Rule (§108, §144)

Four distinct claims, never interchangeable:

- **designed for** — the architecture accounts for the requirement
- **aligned with** — the process follows the guidance
- **tested against** — a specific automated or manual test exercises it
- **certified by** — an external body formally attested it

AgentPay is **not certified** by any external auditing firm. No third-party certification or external
penetration test has taken place. The claims below represent verified internal automated test evidence.

---

## Current Standards Mapping

| Standard | Relationship | Evidence in Repository | Verified Test Suite / Document |
|---|---|---|---|
| **OWASP Top 10 for LLMs** | **tested against** | Prompt injection scanner, advisory model outputs decoupled from deterministic policy, token budget tracking, circuit breakers, immutable tool registry, outbound SSRF filter. | `apps/api/src/agentpay/adapters/model_gateway/model_gateway.py`<br>`apps/api/src/agentpay/domain/tool_registry.py`<br>`tests/adversarial/stress_test_model_gateway_resilience.py`<br>`tests/adversarial/stress_test_ssrf_resilience.py` |
| **OWASP ASVS v4.0.3** | **tested against** | Architectural boundaries, agent lifecycle state machine, exact 9-field bound approvals, constant-time HMAC-SHA256 raw wire signatures, SHA-256 chained audit ledger, fail-closed error shielding. | `apps/api/tests/unit/test_architecture_boundaries.py`<br>`apps/api/tests/unit/test_money.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py`<br>`tests/adversarial/stress_test_webhook_crypto.py` |
| **OWASP API Security Top 10** | **tested against** | Cross-tenant isolation (BOLA/BFLA defense), durable event deduplication replay prevention, standardized §13 error envelopes without traceback leaks. | `apps/api/tests/unit/test_api.py`<br>`apps/api/tests/unit/test_protocol_adapters.py`<br>`tests/adversarial/stress_test_m1_tenant_isolation.py`<br>`tests/adversarial/stress_test_m2_durable_webhooks.py` |
| **ISO 4217 Currency Standards** | **tested against** | Zero IEEE 754 floating-point drift, integer minor units only, 58 currency exponent definitions (0, 2, 3, 4), strict decimal parsing, rational round-half-up FX conversions. | `apps/api/src/agentpay/domain/money.py`<br>`apps/api/src/agentpay/domain/currencies.py`<br>`apps/api/tests/unit/test_money.py`<br>`tests/adversarial/stress_test_money.py` |
| **SLSA 1.2 / CycloneDX 1.5** | **tested against** | Hermetic pinned dependency manifest (`uv.lock`), automated CycloneDX 1.5 JSON SBOM generation with package SHA-256 hashes, in-toto v1 SLSA 1.2 statement. | `pyproject.toml`<br>`uv.lock`<br>`scripts/generate_sbom.py`<br>`apps/api/tests/unit/test_sbom.py` |
| **NIST AI RMF** | **aligned with** | Advisory AI risk classification, governance policy override of advisory outputs, auditable decision summaries without private chain-of-thought. | `apps/api/src/agentpay/domain/decision.py`<br>`docs/security/autonomous-audit-report.md` |
| **NIST SSDF** | **aligned with** | Locked dependency manifest (`uv.lock`), hermetic verification gates, ADR design discipline, tamper-evident audit trail. | `.github/workflows/ci.yml`<br>`docs/decisions/` (ADR-0001, ADR-0002) |
| **OAuth 2.0 Security BCP (RFC 9700)** | **tested against** | Scoped agent identities, audience binding, token expiry enforcement, lifecycle status transitions (`QUARANTINED`, `REVOKED`). | `apps/api/tests/unit/test_identity.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 4) |
| **PCI DSS** | **tested against** | Strict payment data minimization (§40): PAN/CVV redaction prior to hashing, zero cardholder storage, mock PSP tokenization. | `apps/api/tests/unit/test_audit.py` (`test_sensitive_field_redactor`)<br>`apps/api/src/agentpay/domain/audit.py` |
| **India DPDP** | **designed for** | Tenant-level data partitioning, data minimization, cryptographic audit trail. | `apps/api/src/agentpay/domain/audit.py`<br>`apps/api/src/agentpay/adapters/` |

---

## Explicitly Not True Yet

- No standard has been formally certified by an external auditing authority (e.g. SOC 2 Type II, ISO 27001, PCI DSS QSA).
- Commercial SAST or commercial penetration testing by a red team third party has not been conducted; all tests reflect automated internal harness results.
- Open-source vulnerability scanning (Gitleaks, `pip-audit`, Trivy, CodeQL) runs in CI (resolving TD-004), but external commercial certification remains a deployment-owner item.

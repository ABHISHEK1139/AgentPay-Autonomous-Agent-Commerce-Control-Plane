# Standards Matrix

> **Status**: Sprint 12 Complete — Verified Security & Standards Mapping  
> **Last Updated**: 2026-10-07  

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
| **OWASP ASVS** | **tested against** | Architectural boundaries, integer money representation, input validation, fail-closed error shielding. | `apps/api/tests/unit/test_architecture_boundaries.py`<br>`apps/api/tests/unit/test_money.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py` |
| **OWASP API Security Top 10** | **tested against** | Cross-tenant isolation (BOLA/BFLA defense), idempotency replay prevention, standardized §13 error envelopes without traceback leaks. | `apps/api/tests/unit/test_api.py`<br>`apps/api/tests/unit/test_protocol_adapters.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py` (Vectors 2, 3, 13) |
| **OWASP Agentic AI Guidance** | **tested against** | AI strictly below authorization boundary, prompt injection neutralization in external text, fixed tool registry, execution loop budgets (max 5 calls). | `apps/api/tests/unit/test_adversarial_security.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py` (Vectors 5, 6, 7) |
| **NIST AI RMF** | **aligned with** | Advisory AI risk classification, governance policy override of advisory outputs, auditable decision summaries without private chain-of-thought. | `apps/api/src/agentpay/domain/decision.py`<br>`docs/security/autonomous-audit-report.md` |
| **NIST SSDF** | **aligned with** | Locked dependency manifest (`uv.lock`), hermetic verification gates, ADR design discipline, tamper-evident audit trail. | `.github/workflows/ci.yml`<br>`docs/decisions/` (ADR-0001, ADR-0002) |
| **OAuth 2.0 Security BCP (RFC 9700)** | **tested against** | Scoped agent identities, audience binding, token expiry enforcement, lifecycle status transitions (`QUARANTINED`, `REVOKED`). | `apps/api/tests/unit/test_identity.py`<br>`apps/api/tests/unit/test_adversarial_threat_harness.py` (Vector 4) |
| **SLSA** | **aligned with** | Fully reproducible pinned dependency builds via `uv sync --frozen` in CI. | `pyproject.toml`, `uv.lock` |
| **PCI DSS** | **tested against** | Strict payment data minimization (§40): PAN/CVV redaction prior to hashing, zero cardholder storage, mock PSP tokenization. | `apps/api/tests/unit/test_audit.py` (`test_sensitive_field_redactor`)<br>`apps/api/src/agentpay/domain/audit.py` |
| **India DPDP** | **designed for** | Tenant-level data partitioning, data minimization, cryptographic audit trail. | `apps/api/src/agentpay/domain/audit.py`<br>`apps/api/src/agentpay/adapters/` |

---

## Explicitly Not True Yet

- No standard has been formally certified by an external auditing authority (e.g. SOC 2 Type II, ISO 27001, PCI DSS QSA).
- Commercial SAST, automated dependency scanning, or container image scanning tools have not been connected to the CI pipeline (tracked in TD-004).
- External penetration testing by a red team third party has not been conducted; all tests reflect automated internal harness results.

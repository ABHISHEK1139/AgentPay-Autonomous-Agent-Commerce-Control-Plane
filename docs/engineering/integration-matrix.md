# AgentPay — Component Integration Matrix

> **Document Status**: Production Reference Specification  
> **Governing Specifications**: `AgentPay_Implementation_Remediation_Report.md` (§4 Deliverable 1), `AGENTS.md` (§8, §10, §39, §49, §151)  
> **Last Verified**: Oct 12, 2026 (Live Sandbox Re-Verification)

---

## 1. Overview & Vocabulary

Per the remediation plan and `AGENTS.md`, status claims are strictly categorized to avoid confusing simulated test doubles with live external network execution:

- **Implemented**: Code exists in `src/agentpay` and satisfies static types (`mypy --strict`).
- **Unit-Tested**: Exercised in isolated hermetic unit test harnesses.
- **Simulated Integration-Tested (Hermetic CI)**: Component-to-component interaction verified in memory or against local SQLite with simulated gateway responses. Guarantees 100% reproducible, network-independent CI execution.
- **Externally Sandbox-Verified (Live External Gateway)**: Exercised over live outbound HTTPS against the real payment gateway sandbox environment (`https://api.razorpay.com/v1`) using active test API credentials (`rzp_test_...`), verifying real order creation, receipt hashing, authoritative order retrieval, and HMAC-SHA256 signature verification.
- **Production-Ready**: Ready for production deployment with fail-closed security controls (Note: Live-money processing remains disabled per §151).

---

## 2. Master Integration Matrix

| Component | Subsystem | Supported Modes | Real External Dependencies | Interface / Port | Current Status | Verification Evidence | Owner |
|---|---|---|---|---|---|---|---|
| **Razorpay Sandbox Provider** | Payments | `sandbox` | Razorpay Sandbox API (`https://api.razorpay.com/v1`) | `PaymentProvider` | **Hermetic Tested & Externally Sandbox-Verified** (AP-004, AP-017) | Hermetic: `test_m8_sandbox_e2e_reconciliation.py`<br>Live Opt-In: `test_razorpay_sandbox_live_api_order_and_query`<br>Empirical Live Orders: `order_TmxcbPeXFnAdoT`, `order_Tmy5yTfR8ePjXm` | Payments / SAGA |
| **Razorpay Mock Provider** | Payments | `mock`, `development` | None (In-memory simulation) | `PaymentProvider` | Unit-Tested & In-Memory Tested | `apps/api/tests/unit/test_payment_providers.py`<br>`apps/api/tests/unit/test_webhooks.py` | Payments Core |
| **Stripe Simulator** | Payments | `mock`, `development` | None (In-memory simulation; external Stripe sandbox planned) | `PaymentProvider` | Unit-Tested & In-Memory Tested | `apps/api/tests/unit/test_payment_providers.py`<br>`apps/api/tests/unit/test_webhooks.py` | Payments Core |
| **Webhook Ingress & Verifier** | Payments / Security | `mock`, `sandbox` | HTTP transport, raw bytes | Direct route `/api/v1/webhooks/payments` | Unit-Tested & Adversarial-Verified (AP-001, AP-002) | `apps/api/tests/unit/test_webhooks.py`<br>`tests/adversarial/stress_test_webhook_crypto.py` | Security Core |
| **Durable Webhook Event Store** | Persistence / Deduplication | `mock`, `sandbox` | SQLite (verified), PostgreSQL (target) | `WebhookEventRepository` | Integration-Tested (AP-003) | `tests/adversarial/stress_test_m2_durable_webhooks.py`<br>Migration `0002_add_provider_webhook_events.py` | Persistence / DB |
| **Provider Transaction Mapping** | Persistence / Mapping | `mock`, `sandbox` | SQLite (verified), PostgreSQL (target) | `ProviderMappingRepository` | Integration-Tested (AP-001, AP-004) | `tests/adversarial/stress_test_m8_sandbox_e2e_reconciliation.py`<br>Table `provider_mappings` | Payments / DB |
| **Relational Database & Migrations** | Persistence | All | SQLAlchemy 2.0, Alembic, SQLite, PostgreSQL | `BaseSqlAlchemyRepository` | Integration-Tested (AP-007) | `apps/api/tests/unit/test_database_migrations.py`<br>`apps/api/tests/unit/test_database_persistence.py`<br>Migrations `0001`–`0004` | Architecture / DB |
| **Emergency Pause Store** | Governance / Safety | All | SQLite, PostgreSQL, Memory fast path | `EmergencyPauseStorePort` | Integration-Tested (AP-008) | `tests/adversarial/stress_test_m6_shared_operational_controls.py`<br>Migration `0004_add_emergency_pause_records.py` | Operations / Safety |
| **Approval Engine** | Governance | All | SQLite, Memory | `ApprovalRepository` | Unit-Tested & Adversarial-Verified (AP-006) | `tests/adversarial/stress_test_m4_approval_binding.py`<br>`tests/adversarial/stress_test_m4_challenger_edge_cases.py`<br>ADR-0004 | Governance Core |
| **Deterministic Policy Engine** | Authorization | All | None (Pure Python domain) | `SpendingPolicyEngine` | Unit-Tested & Property-Tested | `apps/api/tests/unit/test_policy.py`<br>`apps/api/tests/unit/test_architecture_boundaries.py` | AuthZ Core |
| **Advisory AI Model Gateway** | Intelligence | `mock`, `model_gateway`, `orcarouter`, `openrouter` | OrcaRouter (`https://api.orcarouter.ai/v1`) / OpenRouter (`https://openrouter.ai/api/v1` with `apodex/apodex-1.1-mini:free`) / OpenAI-compatible router | `DecisionEngine` | **Unit-Tested, Hermetic Tested, Adversarial-Verified & Live Verified** (AP-011) | `apps/api/tests/unit/test_openrouter_model_provider.py`<br>`apps/api/tests/unit/test_orcarouter_model_provider.py`<br>`apps/api/tests/unit/test_model_gateway.py`<br>`tests/adversarial/stress_test_model_gateway_resilience.py` | Intelligence |
| **Protocol Adapters (ACP, AP2, UAP, x402)** | Protocols | All | None (Pure translation) | Protocol parsers to `ActionRequest` | Unit-Tested & Adversarial-Verified (AP-012) | `apps/api/tests/unit/test_protocol_adapters.py`<br>`tests/adversarial/stress_test_protocols.py` | Protocols |
| **Observability & Metrics** | Operations | All | Prometheus format, OpenTelemetry conventions | `Tracer`, `Meter`, `/api/v1/metrics` | Unit-Tested (AP-015) | `apps/api/tests/unit/test_api.py`<br>`apps/api/tests/unit/test_trace_correlation.py` | Observability |
| **Supply Chain & Provenance** | Security | All | uv lockfile, CycloneDX 1.5, SLSA 1.2 | CLI `scripts/generate_sbom.py` | Verified Working (AP-013) | `scripts/generate_sbom.py --verify` | Release / Security |

---

## 3. Operational Guarantees by Environment Mode

### 3.1 `mock` / `development` Environment
- Default local developer setup (`AGENTPAY_ENVIRONMENT=development`, `AGENTPAY_PAYMENT_PROVIDER=mock`).
- Zero outbound network calls to external payment processors.
- Simulated payment outcomes, deterministically seeded test catalogs, in-memory or SQLite persistence.
- Fast, hermetic execution for local development and CI quality gates.

### 3.2 `sandbox` Environment
- Dedicated testbed setup (`AGENTPAY_ENVIRONMENT=sandbox`, `AGENTPAY_PAYMENT_PROVIDER=razorpay` or `razorpay_sandbox`).
- Requires valid, non-placeholder credentials (`RAZORPAY_KEY_ID=rzp_test_...`, `RAZORPAY_KEY_SECRET=...`).
- Fails startup closed if credentials are missing, malformed, or contain default placeholder substrings.
- Real outbound HTTPS network requests to `https://api.razorpay.com/v1` with strict connect (5.0s) and read (10.0s) timeouts.
- Authoritative query on timeout before state transitions; SAGA compensation rollbacks on decline/failure.
- Note: External Stripe sandbox adapter is planned for future milestones; currently Stripe uses an in-memory simulator.

### 3.3 `production` Environment (§151 Notice)
- `AGENTPAY_ENVIRONMENT=production`.
- Live-money payment capture is **strictly disabled** in this version of the control plane (§151).
- Requires PostgreSQL database migrations to be applied (`alembic upgrade head`).
- Requires secure, unguessable JWT token secret (`AGENTPAY_TOKEN_SECRET`) and webhook secrets.
- Rejects unauthenticated access to metrics and diagnostics endpoints.

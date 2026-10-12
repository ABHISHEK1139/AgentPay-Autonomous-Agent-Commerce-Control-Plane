# AgentPay — Component Integration Matrix

> **Document Status**: Production Reference Specification  
> **Governing Specifications**: `AgentPay_Implementation_Remediation_Report.md` (§4 Deliverable 1), `AGENTS.md` (§8, §10, §39, §49, §151)  
> **Last Verified**: Remediation Sprint (Commit `39e08b7`)

---

## 1. Overview & Vocabulary

Per the remediation plan and `AGENTS.md`, status claims are strictly categorized:
- **Implemented**: Code exists in `src/agentpay`.
- **Unit-Tested**: Exercised in isolated unit test harnesses.
- **Integration-Tested**: Inter-component communication verified in memory or on local SQLite.
- **Sandbox-Verified**: Real external or mock-simulated provider contract exercised with authoritative queries and webhooks.
- **Production-Ready**: Ready for production deployment with fail-closed security controls (Note: Live-money processing remains disabled per §151).

---

## 2. Master Integration Matrix

| Component | Subsystem | Supported Modes | Real External Dependencies | Interface / Port | Current Status | Verification Evidence | Owner |
|---|---|---|---|---|---|---|---|
| **Razorpay Sandbox Provider** | Payments | `sandbox` | Razorpay Sandbox API (`https://api.razorpay.com/v1`) | `PaymentProvider` | Sandbox-Verified (AP-004, AP-017) | `apps/api/tests/unit/test_payment_providers.py`<br>`tests/adversarial/stress_test_m8_sandbox_e2e_reconciliation.py` | Payments / SAGA |
| **Razorpay Mock Provider** | Payments | `mock` | None (In-memory simulation) | `PaymentProvider` | Unit-Tested & Integration-Tested | `apps/api/tests/unit/test_payment_providers.py`<br>`apps/api/tests/unit/test_webhooks.py` | Payments Core |
| **Stripe Mock Provider** | Payments | `mock` | None (In-memory simulation) | `PaymentProvider` | Unit-Tested & Integration-Tested | `apps/api/tests/unit/test_payment_providers.py`<br>`apps/api/tests/unit/test_webhooks.py` | Payments Core |
| **Webhook Ingress & Verifier** | Payments / Security | `mock`, `sandbox` | HTTP transport, raw bytes | Direct route `/api/v1/webhooks/payments` | Unit-Tested & Adversarial-Verified (AP-001, AP-002) | `apps/api/tests/unit/test_webhooks.py`<br>`tests/adversarial/stress_test_webhook_crypto.py` | Security Core |
| **Durable Webhook Event Store** | Persistence / Deduplication | `mock`, `sandbox` | SQLite (verified), PostgreSQL (target) | `WebhookEventRepository` | Integration-Tested (AP-003) | `tests/adversarial/stress_test_m2_durable_webhooks.py`<br>Migration `0002_add_provider_webhook_events.py` | Persistence / DB |
| **Provider Transaction Mapping** | Persistence / Mapping | `mock`, `sandbox` | SQLite (verified), PostgreSQL (target) | `ProviderMappingRepository` | Integration-Tested (AP-001, AP-004) | `tests/adversarial/stress_test_m8_sandbox_e2e_reconciliation.py`<br>Table `provider_mappings` | Payments / DB |
| **Relational Database & Migrations** | Persistence | All | SQLAlchemy 2.0, Alembic, SQLite, PostgreSQL | `BaseSqlAlchemyRepository` | Integration-Tested (AP-007) | `apps/api/tests/unit/test_database_migrations.py`<br>`apps/api/tests/unit/test_database_persistence.py`<br>Migrations `0001`–`0004` | Architecture / DB |
| **Emergency Pause Store** | Governance / Safety | All | SQLite, PostgreSQL, Memory fast path | `EmergencyPauseStorePort` | Integration-Tested (AP-008) | `tests/adversarial/stress_test_m6_shared_operational_controls.py`<br>Migration `0004_add_emergency_pause_records.py` | Operations / Safety |
| **Approval Engine** | Governance | All | SQLite, Memory | `ApprovalRepository` | Unit-Tested & Adversarial-Verified (AP-006) | `tests/adversarial/stress_test_m4_approval_binding.py`<br>`tests/adversarial/stress_test_m4_challenger_edge_cases.py`<br>ADR-0004 | Governance Core |
| **Deterministic Policy Engine** | Authorization | All | None (Pure Python domain) | `SpendingPolicyEngine` | Unit-Tested & Property-Tested | `apps/api/tests/unit/test_policy.py`<br>`apps/api/tests/unit/test_architecture_boundaries.py` | AuthZ Core |
| **Advisory AI Model Gateway** | Intelligence | `mock`, `live` | Model provider endpoint (Gemini / Anthropic / OpenAI) | `DecisionEngine` | Unit-Tested & Adversarial-Verified (AP-011) | `apps/api/tests/unit/test_model_gateway.py`<br>`tests/adversarial/stress_test_model_gateway_resilience.py` | Intelligence |
| **Protocol Adapters (ACP, AP2, UAP, x402)** | Protocols | All | None (Pure translation) | Protocol parsers to `ActionRequest` | Unit-Tested & Adversarial-Verified (AP-012) | `apps/api/tests/unit/test_protocol_adapters.py`<br>`tests/adversarial/stress_test_protocols.py` | Protocols |
| **Observability & Metrics** | Operations | All | Prometheus format, OpenTelemetry conventions | `Tracer`, `Meter`, `/api/v1/metrics` | Unit-Tested (AP-015) | `apps/api/tests/unit/test_api.py`<br>`apps/api/tests/unit/test_trace_correlation.py` | Observability |
| **Supply Chain & Provenance** | Security | All | uv lockfile, CycloneDX 1.5, SLSA 1.2 | CLI `scripts/generate_sbom.py` | Verified Working (AP-013) | `scripts/generate_sbom.py --verify` | Release / Security |

---

## 3. Operational Guarantees by Environment Mode

### 3.1 `mock` Environment
- Default local developer setup (`AGENTPAY_ENVIRONMENT=development`, `AGENTPAY_PAYMENT_PROVIDER=mock`).
- Zero network calls to external payment processors.
- Simulated payment outcomes, deterministically seeded test catalogs, in-memory or SQLite persistence.
- Fast execution for local development and CI unit tests.

### 3.2 `sandbox` Environment
- Dedicated testbed setup (`AGENTPAY_ENVIRONMENT=sandbox`, `AGENTPAY_PAYMENT_PROVIDER=razorpay`).
- Requires valid, non-placeholder credentials (`RAZORPAY_KEY_ID=rzp_test_...`, `RAZORPAY_KEY_SECRET=...`).
- Fails startup closed if credentials are missing or default demo secrets are detected.
- Outbound network requests to provider sandbox APIs with strict connect (5.0s) and read (10.0s) timeouts.
- Authoritative query on timeout before state transitions; SAGA compensation rollbacks on decline/failure.

### 3.3 `production` Environment (§151 Notice)
- `AGENTPAY_ENVIRONMENT=production`.
- Live-money payment capture is **strictly disabled** in this version of the control plane (§151).
- Requires PostgreSQL database migrations to be applied (`alembic upgrade head`).
- Requires secure, unguessable JWT token secret (`AGENTPAY_TOKEN_SECRET`) and webhook secrets.
- Rejects unauthenticated access to metrics and diagnostics endpoints.

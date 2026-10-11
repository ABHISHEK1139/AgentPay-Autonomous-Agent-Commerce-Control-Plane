# Current State

Verified on this machine (Windows, PowerShell 5.1) with Python 3.12.10, Node v22.14.0, uv 0.12.22, ruff 0.16.10,
mypy 2.4.0, pytest 9.1.1.

## What exists

| Component | State | Scope & Verification |
| --- | --- | --- |
| Component | State | Scope & Verification |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | locked | Dependencies locked (including `sqlalchemy>=2.0.0`), zero forbidden imports in domain |
| Monorepo layout (§9) | verified | `apps/api`, `apps/web`, `apps/worker`, `packages/*`, `scripts/`, `security/`, `tests/`, `docs/*`. |
| Version Control | active | Git initialized, provenance tracked |
| Domain: `Money` & ISO 4217 `currencies` | implemented | 58 ISO 4217 currencies (0/2/3-decimal exponents), integer decimal parsing, rational `ExchangeRate` (§122, §123, TD-002 resolved) |
| Domain: `ActionRequest` | implemented | Canonical lifecycle abstraction (§6, §7) |
| Domain: `Identity & Tenancy` | implemented | Tenant, User, Agent, AgentPermissions, Credential, TokenClaims (§14, §15, §16, §49) |
| Domain: `Commerce & Evidence` | implemented | Merchant, Product, Variant, Inventory, Conflict Detector (§30, §32) |
| Domain: `Decision & Approval` | implemented | JEV Decision models with prompt/tool/policy metadata (§29, §118), ApprovalBinding, state machine (§18, §20) |
| Domain: `Payments & Orders` | implemented | PaymentAttempt, Order, Idempotency, Saga compensation (§39, §41, §42) |
| Domain: `Audit Ledger` | implemented | SHA-256 hash chaining, sensitive field redaction (§29, §45) |
| Domain: `Safety & Protection` | implemented | Loop tracker, tool call budget, financial safety budget, sliding window RateLimiter (§52, §62, §64, §65) |
| Domain: `Tool Registry` | implemented | Fixed tool registry, ToolSpec/ToolDefinition, capability gating, risk classification (§26, §27) |
| Domain: `Outbox & SecurityEvent` | implemented | `OutboxEvent` transactional lifecycle (§106), `SecurityEvent` abuse detection models (§62, §63) |
| Application Layer | implemented | `AgentAuthorizationService`, `ApprovalService`, `PaymentReconciler`, `PurchaseOrchestrator`, `EventBus`, `AbuseDetector` |
| Resilience & SSRF Filter | implemented | `CircuitBreaker` (3-state machine), `RetryPolicy` (financial non-retryability), Outbound SSRF validator (§60, §102, §103) |
| AI Model Gateway & Evaluation | implemented | `ModelGateway` with `TokenBudgetTracker`, fail-closed fallback, and versioned evaluation dataset (`dataset_v1.json`, §23, §117) |
| Background Worker Runtime | implemented | `OutboxDispatcher`, `BackgroundWorker` single-tick and loop execution in `apps/worker` and `apps/api/workers` (§104, §106) |
| Observability Subsystem | implemented | Zero-dependency OpenTelemetry-aligned `Tracer`, `Span`, `Meter`, `Counter`, `Histogram`, and `GET /api/v1/metrics` (§47) |
| Supply Chain & Provenance | implemented | CycloneDX 1.5 JSON SBOM & SLSA 1.2 build provenance attestation CLI generator (`scripts/generate_sbom.py`, §57, §130) |
| Protocol Adapters | implemented | ACP, AP2, UAP, x402 adapters mapping to canonical `ActionRequest` (§107, §108) |
| Developer SDK | implemented | Thin typed Python client (`from agentpay import AgentPay`, §109) |
| Web Control Center | implemented | `apps/web/`: 10-step inspectable pipeline, live API client integration (§14, §21, §66, §67), approval UI (§66), kill switch UI (§67), ARIA accessibility & keyboard navigation (§121) |
| Ports & Adapters | implemented | Thread-safe in-memory repositories, Stripe & Razorpay payment adapters with HMAC-SHA256 signature verification and anti-replay windows, mock JEV decision engine |
| Relational Persistence (§49) | implemented | SQLAlchemy 2.0 models and tenant-scoped repositories (`infrastructure/database/`), Alembic migrations `0001_initial_schema` covering all 17 tables, verified on in-memory SQLite and offline SQL DDL generation |
| HTTP API, Webhooks & Emergency Controls | implemented | Standard error envelopes with trace_id (§13, §47), OpenAPI 3.1, HMAC-SHA256 Payment Webhooks (`POST /api/v1/webhooks/payments`, §61), Kill switch (§21, §67), strict least-privilege `ApiTestClient` with explicit caller persona requirements |
| Operational Runbooks | implemented | All 9 named runbooks in `docs/runbooks/` (§133) |
| Engineering Journal | implemented | `docs/engineering/experiments.md` documenting EXP-001 through EXP-006 (§87) |
| Security Assurance | implemented | OWASP ASVS v4.0.3 verification matrix, YAML index, exceptions in `security/asvs/` (§53) |
| CLI & Server Runtime | implemented | Zero-dependency multi-threaded HTTP server (`server.py`), CLI runner (`cli.py`), application factory (`create_app()`) |
| Standalone Packages | implemented | `packages/sdk`, `packages/protocol-types`, `packages/schemas`, `packages/test-utils` per §9 |
| Live Socket E2E Network Tests | implemented | Real TCP socket integration suite verifying server, SDK, and HMAC webhooks |
| Failure Engineering (§112) | verified | Chaos suite: price drift, inventory depletion, timeout reconciliation, saga |
| Red-Team Security & 14 Vectors (§177) | verified | Exhaustive adversarial threat harness, crypto tamper suite, concurrency stress suite |
| Architecture Boundary Guard | verified | 7 tests enforce domain purity (§10) with zero external imports |

Verified command results:

```text
uv run ruff format --check .                                            175 files already formatted
uv run ruff check .                                                     All checks passed!
uv run mypy                                                             Success: no issues found in 167 source files
uv run pytest                                                           748 passed
node --test tests/web.test.mjs                                          5 passed (incl. live API & §121 accessibility)
uv run python tests/adversarial/stress_test_protocols.py                70/70 passed
uv run python tests/adversarial/stress_test_concurrency.py              4/4 passed
uv run python tests/adversarial/stress_test_client_privilege.py          6/6 passed (incl. unconfigured fail-closed)
uv run python tests/adversarial/stress_test_money.py                    6/6 passed
uv run python tests/adversarial/stress_test_webhooks.py                 6/6 passed
uv run python tests/adversarial/stress_test_ssrf_resilience.py          9/9 passed
uv run python tests/adversarial/stress_test_model_gateway_resilience.py  6/6 passed
uv run python scripts/verify_audit_chain.py --help                      Verified working
uv run python scripts/generate_sbom.py --verify                          Verified working (CycloneDX 1.5 + SLSA 1.2)
```

All property-based tests completed their full budgets.

## Environment constraints

- `make`, `just`, and `psql` are **not installed**. The blueprint's §9 `Makefile`/`justfile` are
  therefore not provided; use the `uv run` and `node --test` commands instead.
- The **Docker daemon is not running**, so the declared Postgres/Redis services cannot start. All
  domain, application, API, protocol adapter, chaos, and security suites are hermetic and run entirely in-memory.
- The project directory is named `platfrom` (one `a`). That is the intended path.
- Shell is PowerShell 5.1: no `&&`, no heredocs.

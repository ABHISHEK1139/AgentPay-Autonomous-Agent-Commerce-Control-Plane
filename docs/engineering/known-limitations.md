# Known Limitations

Things a future engineer will otherwise discover the hard way.

## Operational & Environment Limitations

- **No Running Docker Daemon Locally**: The local development machine does not have a running Docker daemon
  (`docker info` fails). All 566 automated backend tests and 3 web dashboard tests run hermetically against
  thread-safe in-memory repository adapters, in-memory SQLite (for SQLAlchemy 2.0 relational repositories in
  `test_database_persistence.py`), and the mock payment provider. Postgres and Redis are declared in
  `docker-compose.yml` / `.env.example`, and SQLAlchemy 2.0 models and repositories exist under
  `apps/api/src/agentpay/infrastructure/database/`, but live PostgreSQL execution and Alembic migration scripts
  have not been exercised on this machine.
- **Task Runner Tools**: `make` and `just` are not installed on this machine (TD-003). All operations
  are executed directly via `uv run` and `node --test`.

## Domain & Commerce Limitations

- **Currency Minor-Unit Exponents (TD-002 — Resolved in Sprint 14)**: `Money` now enforces an explicit ISO 4217
  exponent table (`domain/currencies.py`) covering 58 currencies across 0-decimal (e.g., `JPY`, `KRW`),
  2-decimal (e.g., `INR`, `USD`, `EUR`), and 3-decimal (e.g., `BHD`, `KWD`, `OMR`) exponents, with integer-only
  decimal string parsing/formatting and rational `ExchangeRate` conversion. Unregistered currency codes fail closed.
- **Single-Item Atomic Purchase Slice**: While the data model supports carts and multi-item orders, the primary
  hardened orchestrator (`PurchaseOrchestrator`) specializes in atomic single-variant purchase actions with
  pre-flight conflict detection.
- **Mock Payment Provider Only**: The payment subsystem uses a mock provider (`MockPaymentProvider`,
  labelled `mock_razorpay`) implementing the payment state machine (`CREATED`, `AUTHORIZED`,
  `CAPTURED`, `FAILED`, `UNKNOWN`, `REFUNDED`), idempotency checking, and saga compensation. Incoming
  payment webhooks (`POST /api/v1/webhooks/payments`) are verified with HMAC-SHA256 and 300s replay windows,
  but there is no live external PSP gateway SDK in `src`.

## AI & Intelligence Boundaries

- **Advisory AI Simulation**: The decision engine (JEV / LLM) operates via mock and structured decision adapters
  to ensure deterministic, reproducible test execution. While external LLM gateways can be plugged in via
  the application decision interface, live model API keys are omitted from hermetic test suites.
- **Deterministic Policy Priority (§2.2, §134)**: Even with advanced model integrations, the policy engine
  strictly refuses any model recommendation that exceeds configured financial thresholds.

## Security & Scanning

- **CI Scanning Tools (TD-004)**: Static type checking (`mypy`), linting (`ruff`), and internal adversarial
  harnesses run on every commit. Third-party commercial SAST, container vulnerability scanners, and external
  penetration testing have not been conducted.

## Blueprint Status Matrix (Sprint 15 Verified on 2026-10-11)

| § | Requirement | Status |
| --- | --- | --- |
| 47 | Observability: OpenTelemetry traces, metrics, `trace_id` propagation | **Implemented (Sprint 15)** — Zero-dependency OpenTelemetry-aligned `Tracer`, `Span`, `Meter`, `Counter`, `Histogram`, Prometheus exposition format, and mounted `GET /api/v1/metrics` |
| 60 | SSRF protection (scheme/host allowlists, private-range blocking) | **Implemented (Sprint 15)** — `security/ssrf.py` with HTTPS scheme allowlists, host allowlists, DNS pre-flight anti-rebinding, and IP blocking for loopback, RFC 1918, IMDS `169.254.169.254`, link-local, and IPv4-mapped IPv6 |
| 61 | Payment webhooks with signature verification and replay protection | **Implemented (Sprint 14)** — `POST /api/v1/webhooks/payments` enforces HMAC-SHA256 (`hmac.compare_digest`), 300s freshness window, mutex-guarded idempotency replay defense, and SAGA state-machine guards |
| 62, 63 | Abuse detection / security events (credential stuffing, tool loops, scraping) | **Implemented (Sprint 15)** — `SecurityEvent` domain model, `AbuseDetector` application service for credential stuffing, scraping bursts, tool-loop exhaustion, and spend velocity spikes, with automatic agent quarantine/suspension |
| 102, 103 | Retries with backoff, circuit breakers | **Implemented (Sprint 15)** — `CircuitBreaker` (CLOSED/OPEN/HALF_OPEN state machine) and `RetryPolicy` strictly enforcing financial non-retryability |
| 104, 105, 106 | Background workers, event bus, transactional outbox | **Implemented (Sprint 15)** — `OutboxEvent` domain model, `OutboxDispatcher`, `EventBus`, and `BackgroundWorker` runtime for outbox dispatch, stuck payment reconciliation, and expired approval sweeps |
| 117 | Versioned model evaluation dataset | **Implemented (Sprint 15)** — Versioned benchmark dataset `tests/fixtures/evaluation/dataset_v1.json` with 10 labeled test cases covering all 4 decision outcomes |
| 121 | Accessibility: keyboard navigation, ARIA roles, screen-reader support | **Implemented (Sprint 15)** — ARIA dialog roles, live regions, tablist/tabs/tabpanel bindings, accessible button labels, and keyboard Escape dismissal across `apps/web/src` |
| 130, 57 | SBOM generation, build provenance, SLSA evidence | **Implemented (Sprint 15)** — `scripts/generate_sbom.py` generating CycloneDX 1.5 JSON SBOM (23 components) and SLSA 1.2 build provenance attestation, with `--verify` validation CLI |
| 49 | PostgreSQL persistence and migrations | **Partial** — SQLAlchemy 2.0 models and tenant-scoped repositories implemented (`infrastructure/database/`) and verified against in-memory SQLite; live PostgreSQL daemon and Alembic migrations not yet exercised locally |
| 23 | Model gateway: timeout, retry, fallback, token budget | **Implemented (Sprint 15)** — `ModelGateway` with `TokenBudgetTracker`, prompt-injection heuristics, and non-widening fail-closed fallback |

### Test Client Privilege Model (Hardened in Sprint 14)

`ApiTestClient.request()` defaults to `auth=True` with **least-privilege `UserRole.BUYER`** (hardened from the
prior `UserRole.ADMIN` default). Administrative endpoints (such as the emergency kill switch) reject default
`ApiTestClient` requests with `403 UNAUTHORIZED_OPERATOR` unless invoked via `.as_admin()`. Tests verifying
unauthenticated rejection should use `.as_unauthenticated()` or pass `auth=False`.

## Can this platform move real money yet?

**No.** What exists is verified domain logic, HMAC-verified webhook ingestion, and SQLAlchemy 2.0 relational
repositories verified on in-memory SQLite. Missing before any real financial exposure: a live PostgreSQL
deployment with Alembic migrations, a real payment provider SDK integration, external security assessment,
and a DR exercise that has actually been performed.

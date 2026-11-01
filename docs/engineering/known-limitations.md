# Known Limitations

Things a future engineer will otherwise discover the hard way.

## Operational & Environment Limitations

- **External Services Are Not Required by the Test Suite**: All 904 automated backend tests and
  6 web dashboard tests run hermetically against thread-safe in-memory repository adapters,
  in-memory SQLite (for SQLAlchemy 2.0 relational repositories in `test_database_persistence.py`),
  and the mock payment provider. Postgres and Redis are declared in `docker-compose.yml` /
  `.env.example`; live local validation (PostgreSQL migrations, Redis, dump/restore) was performed
  with the Docker daemon running, separately from the hermetic suite; see
  `docs/engineering/current-state.md` for the recorded results.
- **Task Runner Tools**: `make` and `just` are installed via winget but are **not on the default
  PATH**; prepend the winget package bin directories (see `docs/engineering/current-state.md`) or
  use the `uv run` / `node --test` commands directly. `Makefile` and `justfile` exist and delegate
  to the verified commands (TD-003 resolved).

## Domain & Commerce Limitations

- **Currency Minor-Unit Exponents (TD-002 — Resolved in Sprint 14)**: `Money` now enforces an explicit ISO 4217
  exponent table (`domain/currencies.py`) covering 58 currencies across 0-decimal (e.g., `JPY`, `KRW`),
  2-decimal (e.g., `INR`, `USD`, `EUR`), and 3-decimal (e.g., `BHD`, `KWD`, `OMR`) exponents, with integer-only
  decimal string parsing/formatting and rational `ExchangeRate` conversion. Unregistered currency codes fail closed.
- **Multi-Item Cart Checkout (Resolved — ADR-0006)**: The `Cart` aggregate, tenant-scoped
  `CartRepository` (in-memory + SQLAlchemy, migration `0005_add_cart_tables`), agent-facing cart
  endpoints (`POST/GET /agent/carts...`), and the approval-aware `/agent/checkout` orchestration
  (one payment, N single-variant orders, saga compensation, cart-bound idempotency) are implemented
  and tested. MVP semantics bind each cart to a single merchant; cross-merchant carts fail closed.
  The atomic single-variant purchase slice remains available and unchanged. Concurrent checkouts of
  the same cart are serialized by **process-local** striped locks in `PurchaseOrchestrator`; a
  multi-process / multi-replica deployment would additionally require a database-level atomic claim
  on the cart (the duplicate-payment race this guards against is regression-tested in
  `test_cart_flow.py` §9).
- **Mock and Sandbox Payment Providers (No Live Money, §151)**: The payment subsystem provides
  both hermetic in-memory mock adapters (`MockPaymentProvider`) and a dedicated sandbox adapter
  (`RazorpaySandboxProvider`) implementing authoritative status queries, SAGA inventory rollback,
  HMAC-SHA256 signature verification, and durable database-backed webhook deduplication. Live-money
  production execution is strictly disabled (§151).

## AI & Intelligence Boundaries

- **Advisory AI Simulation**: The decision engine (JEV / LLM) operates via mock and structured decision adapters
  to ensure deterministic, reproducible test execution. While external LLM gateways can be plugged in via
  the application decision interface, live model API keys are omitted from hermetic test suites.
- **Deterministic Policy Priority (§2.2, §134)**: Even with advanced model integrations, the policy engine
  strictly refuses any model recommendation that exceeds configured financial thresholds.

## Security & Scanning

- **CI Scanning Coverage (TD-004 — Resolved)**: CI now runs a dedicated `security-scan` job:
  secrets (gitleaks, configured in `.gitleaks.toml`), dependencies (`scripts/dependency_audit.py`,
  pip-audit against `uv.lock`, fails closed), and filesystem vulnerabilities (Trivy, HIGH/CRITICAL
  fail the build), plus CodeQL in a separate workflow. These are automated scanner *evidence*, not
  proof of absolute security. Commercial SAST suites and external penetration testing have **not**
  been conducted.

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
| 130, 57 | SBOM generation, build provenance, SLSA evidence | **Implemented (Sprint 15)** — `scripts/generate_sbom.py` generating CycloneDX 1.5 JSON SBOM (29 components) and SLSA 1.2 build provenance attestation, with `--verify` validation CLI |
| 49 | PostgreSQL persistence and migrations | **Implemented** — SQLAlchemy 2.0 models and tenant-scoped repositories implemented (`infrastructure/database/`), verified against in-memory SQLite and against live local PostgreSQL with Alembic migrations (dump/restore verified separately) |
| 23 | Model gateway: timeout, retry, fallback, token budget | **Implemented (Sprint 15)** — `ModelGateway` with `TokenBudgetTracker`, prompt-injection heuristics, and non-widening fail-closed fallback |

### Test Client Privilege Model (Hardened in Sprint 14)

`ApiTestClient.request()` defaults to `auth=True` with **least-privilege `UserRole.BUYER`** (hardened from the
prior `UserRole.ADMIN` default). Administrative endpoints (such as the emergency kill switch) reject default
`ApiTestClient` requests with `403 UNAUTHORIZED_OPERATOR` unless invoked via `.as_admin()`. Tests verifying
unauthenticated rejection should use `.as_unauthenticated()` or pass `auth=False`.

## Can this platform move real money yet?

**No.** What exists is verified domain logic, HMAC-verified webhook ingestion, SQLAlchemy 2.0 relational
repositories verified on in-memory SQLite, and separate live local validation of PostgreSQL migrations,
Redis, the Razorpay sandbox order path, and PostgreSQL dump/restore. Missing before any real financial
exposure: a production-grade deployment, a real payment provider SDK integration, external security
assessment, and a DR exercise performed against production-like infrastructure.

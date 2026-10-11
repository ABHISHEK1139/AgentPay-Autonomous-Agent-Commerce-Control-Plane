# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-11

### Added
- **Production CLI & Server Runner**:
  - `agentpay` CLI entrypoint (`agentpay serve`, `agentpay worker`, `agentpay health`, `agentpay migrate`, `agentpay version`).
  - Pure standard-library multi-threaded HTTP server (`agentpay.server.ThreadingHTTPServer`) with zero third-party web framework dependencies.
  - Full CORS pre-flight negotiation, security response headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`), socket draining on close, and optional SPA static file hosting for `apps/web/dist`.
  - Application factory (`create_app()`) supporting hermetic in-memory or SQLAlchemy 2.0 relational SQLite/PostgreSQL repositories with automated baseline demo data seeding.
- **Standalone Packages**:
  - `packages/sdk`: Standalone `agentpay-sdk` providing typed Python client (`from agentpay_sdk import AgentPayClient`) using pure standard library HTTP transport.
  - `packages/protocol-types`: Standalone `agentpay-protocol-types` re-exporting external commerce protocols (ACP, AP2, UAP, x402).
  - `packages/schemas`: Standalone `agentpay-schemas` providing canonical JSON schemas for `ActionRequest` and API error envelopes.
  - `packages/test-utils`: Standalone `agentpay-test-utils` offering in-process client doubles and test fixtures.
- **Live Socket Network E2E Integration Suite**:
  - `apps/api/tests/unit/test_live_server_e2e.py` covering live TCP socket binding, `/api/v1/health`, `/api/v1/openapi.json`, `/api/v1/metrics`, CORS preflight, Python SDK catalog discovery, autonomous purchase execution, human approval threshold gating, emergency pause 503 fail-closed enforcement, and HMAC-SHA256 signed payment webhooks.
- **Dedicated Approved Execution Endpoint**:
  - Added `POST /api/v1/approvals/{id}/execute` endpoint and `approval_id` mapping on `POST /api/v1/actions` and `POST /agent/actions` (B-API-02).
- **Supply Chain & Provenance**:
  - `scripts/generate_sbom.py`: CycloneDX 1.5 JSON SBOM and SLSA 1.2 build provenance attestation CLI with self-verification (`--verify`).
  - `scripts/verify_audit_chain.py`: Command-line verifier for SHA-256 chained audit ledgers.
- **Onboarding & Operations Documentation**:
  - `docs/onboarding/quickstart.md`: 5-minute zero-to-production quickstart guide covering setup, live server launch, outbox worker, Python SDK, human approval, and emergency kill switch operations.
  - `docs/onboarding/developer.md`: Complete developer onboarding guide.

### Changed
- Expanded verified test suite to **748 passing tests** (0 failed, 0 skipped).
- Expanded strict `mypy` type checking across all packages: `apps/api/src`, `apps/api/tests`, `apps/worker/src`, `infrastructure`, `scripts` (167 source files clean).
- Clean code formatting across all 175 source files (`ruff format --check .` and `ruff check .`).
- Updated package version to `0.1.0` in `pyproject.toml` and registered console scripts.
- Added database persistence artifacts (`*.db`, `*.sqlite`, `*.sqlite3`) to `.gitignore`.

### Fixed
- **B-REF-01 (CRITICAL)**: Hardened refund validation across `MockPaymentProvider`, `StripePaymentProvider`, and `RazorpayPaymentProvider` enforcing positive minor units, currency equality, refundable state validation (`CAPTURED` or `PARTIALLY_REFUNDED`), and cumulative refund balance limits. Correctly transitions payment state to `PARTIALLY_REFUNDED` vs `REFUNDED`.
- **B-IDEM-01 (HIGH)**: Re-ordered `PurchaseOrchestrator.execute_approved_purchase` to evaluate the idempotency store before consuming single-use approval tokens, enabling transparent network retries without raising `ApprovalAlreadyConsumedError`.
- **B-IDEM-02 (MEDIUM)**: Added currency verification in `PurchaseOrchestrator` idempotency conflict checks and stored currency code in `IdempotencyRecord.result_data`, preventing cross-currency replay bugs.
- **B-AUTH-02 (CRITICAL)**: Validated agent active status, expiration timestamp, and category permissions in `execute_approved_purchase`.
- **B-DRIFT-01 (HIGH)**: Re-verified live merchant status, catalog pricing drift, inventory availability, and evidence conflicts prior to executing approved purchases.
- **B-REC-01 (MEDIUM)**: Replaced private attribute sniffing in `PaymentReconciler` with formal `find_by_payment_attempt` port protocol method implemented on all order repositories.
- **B-API-01 (MEDIUM)**: Validated JSON request body type on mutating endpoints, returning HTTP 422 `INVALID_REQUEST_BODY` for non-dict payloads instead of raising unhandled `AttributeError`.
- **B-OUT-01 (LOW)**: Implemented atomic distributed outbox worker claiming, 60s lease timeouts, dead-worker lease recovery, and exponential backoff retry scheduling.
- **B-WH-01..04**: Fixed webhook HMAC validation over raw bytes, ±300s timestamp freshness window, explicit tenant resolution, and per-event replay deduplication.
- **B-PAUSE-01**: Enforced scoped tenant-level emergency pause controls.
- **B-RED-01**: Hardened case-insensitive audit redaction across all sensitive PCI/PII field variants.

### Security
- Empirically verified defense against all 14 mandatory attack vectors defined in Blueprint §177.
- Enforced strict least-privilege API client requiring explicit caller personas or bearer tokens, failing closed on unauthenticated or unconfigured requests.
- Enforced HMAC-SHA256 cryptographic webhook verification with timestamp tolerance window and replay locking.
- Guaranteed deterministic policy engine supremacy: AI models and LLM/JEV outputs sit strictly below the authorization boundary.

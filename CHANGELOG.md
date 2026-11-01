# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Multi-Item Cart Checkout (ADR-0006, §38, §41, §44)**: `Cart` domain aggregate (single-merchant,
  merge/remove/lifecycle invariants), tenant-scoped `CartRepository` port with in-memory and
  SQLAlchemy 2.0 implementations, and Alembic migration `0005_add_cart_tables` (`carts`, `cart_items`).
- **Agent-Facing Cart API (§38)**: `POST /agent/carts`, `GET /agent/carts/{id}`,
  `POST /agent/carts/{id}/items`, `POST /agent/carts/{id}/items/remove`, and multi-item
  `POST /agent/checkout` with asserted-total verification, per-line price/inventory conflict
  checks, emergency-pause fail-closed behavior, and agent impersonation denial. Cart checkouts
  bind 8-field approvals to the cart (`action="checkout"`, `resource.type="cart"`) and execute
  through `POST /api/v1/approvals/{id}/execute`.
- **Cart Checkout Orchestration**: reserve-all → one payment → N single-variant CONFIRMED orders
  (shared `payment_attempt_id`) → cart `CHECKED_OUT`, with saga compensation (release reservations /
  refund) on partial failure, UNKNOWN-payment reservation retention, and cart-bound idempotency.
- **Task Runner Entry Points (TD-003)**: `Makefile` and `justfile` delegating to the verified
  `uv run` / `node --test` commands (`make all` / `just all` run the full gate battery).
- **CI Security Scanning Job (TD-004)**: `.gitleaks.toml` secret scanning, `scripts/dependency_audit.py`
  (pip-audit against `uv.lock`, fail-closed), and Trivy filesystem vulnerability scanning in
  `.github/workflows/ci.yml`, plus live local PostgreSQL/Redis/Docker validation.
- **Cart Test Coverage**: `test_cart.py` (domain invariants), `test_cart_flow.py` (19 orchestrator
  scenarios incl. approvals, saga compensation, idempotency, cross-tenant denial, and
  per-cart checkout-lock concurrency regressions),
  `test_api_cart.py` (HTTP lifecycle, impersonation, pause, error envelopes, OpenAPI/discovery
  contract), and cart persistence and migration-chain coverage.
- **Agent-Facing Evidence API (§32, §38)**: `GET /agent/products/{id}/evidence` serves the persisted catalog provenance chain (price, inventory, specification, merchant) for a product or variant, with tenant partitioning and fail-closed 404 `PRODUCT_NOT_FOUND`.
- **Executable Read-Only Tools (§26, §27)**: `get_evidence`, `compare_products` (live availability + lowest-price ranking), and `calculate_total` (exact integer minor-unit arithmetic) are now genuinely implemented in `PurchaseOrchestrator.execute_tool`.
- **Domain Error `TOOL_EXECUTION_UNAVAILABLE`**: fail-closed `ToolExecutionUnavailableError` for registered-but-unimplemented tools, mapped to HTTP 501 with guidance to the canonical action/approval endpoints.

### Changed
- Verified gate counts refreshed: 904 Python tests passing (3 opt-in skips), mypy clean across
  194 source files, ruff clean across 211 files, 6 web tests passing.

### Fixed
- **B-WEB-01 (HIGH)**: Web approval resolution now calls the backend (grant/reject with `res.ok` checks, then chained execution on approval), surfaces execution failures instead of fabricating success, synchronizes live approval data, and keeps the deterministic demo simulation only in demo mode.
- **B-TOOL-01 (MEDIUM)**: `execute_tool` no longer returns fabricated `"status": "executed"` for unimplemented tools — it fails closed with `TOOL_EXECUTION_UNAVAILABLE` (§2.4, §135).
- **B-EVID-01 (MEDIUM)**: Purchases now persist immutable catalog evidence snapshots through `EvidenceRepository`, making recommendation provenance auditable after the fact (§2.3, §32).
- **B-MERGE-01 (HIGH)**: `AgentPayApiApp` now accepts the `refund_repo` argument passed by `create_app()`, wiring it into the `PaymentReconciler` instead of raising `TypeError` on app construction.
- **B-MERGE-02 (HIGH)**: Approval binding verification now compares the resolved merchant identity (variant→product→merchant) on both creation and execution sides, matching the §20 exact-binding contract.
- **B-MERGE-03 (MEDIUM)**: Generic, Razorpay, and Stripe webhook handlers fail closed with `500 WEBHOOK_SECRET_NOT_CONFIGURED` when no signing secret is configured instead of raising `TypeError` → `INTERNAL_ERROR`.
- **B-MERGE-04 (MEDIUM)**: Restored the deleted capability-discovery and catalog-filter tests and corrected wrong OpenAPI/inventory assertions in `test_api.py` (§13, §78).
- **B-MERGE-05 (MEDIUM)**: Restored the provider-neutral `AGENTPAY_MODEL_*` OpenAI-compatible configuration (fields, env parsing, validation rule 5b, factory wiring) that was silently removed while still documented in the sandbox runbook.
- **B-MERGE-06 (LOW)**: Worker approval sweeps use the dedicated `EXPIRED` state; tests and adversarial harnesses updated to the `EXPIRED` semantics.
- **B-MERGE-07 (LOW)**: Resolved the merge's `mypy --strict` and `ruff` regressions (payment attempt annotation, `str | None` webhook secrets, import ordering).
- **B-SRV-01 (LOW)**: The live-server oversized-body rejection test now tolerates the Windows TCP reset race (WinError 10053) while still asserting the 413 `PAYLOAD_TOO_LARGE` envelope on every received response; success responses still hard-fail.
- **B-CART-01 (HIGH)**: Two concurrent checkouts of the same ACTIVE cart with distinct idempotency keys could both pass the ACTIVE-cart check and reach the payment pipeline (duplicate payment, duplicate orders, and a lost inventory reservation update). Cart checkout is now serialized by process-local striped per-`(tenant, cart)` locks spanning decision → reservation → payment → `CHECKED_OUT` in both `execute_cart_purchase` and `execute_approved_cart_purchase`; the loser observes `CHECKED_OUT` and fails closed. Regression tests: deterministic lock-contract test, 3-round barrier race with injected decision latency, and a same-key concurrent replay test that must yield one payment. Multi-process deployments still require a database-level cart claim (recorded in Known Limitations).
- **B-API-03 (LOW)**: `/openapi.json` and `/.well-known/agent-commerce` now publish the cart surface (`POST /agent/carts`, `GET /agent/carts/{cart_id}`, `POST /agent/carts/{cart_id}/items`, `POST /agent/carts/{cart_id}/items/remove`, `POST /agent/checkout`, plus `commerce.carts` / `commerce.cart_checkout` discovery keys), with contract assertions in `test_api_cart.py` (§13, §38).

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

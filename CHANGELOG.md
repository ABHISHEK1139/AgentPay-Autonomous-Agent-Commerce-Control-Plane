# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-11

### Added
- **Production CLI & Server Runner**:
  - `agentpay` CLI entrypoint (`agentpay serve`, `agentpay worker`, `agentpay health`, `agentpay migrate`, `agentpay version`).
  - Pure standard-library multi-threaded HTTP server (`agentpay.server.ThreadingHTTPServer`) with zero third-party web framework dependencies.
  - Full CORS pre-flight negotiation, security response headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`), and optional SPA static file hosting for `apps/web/dist`.
  - Application factory (`create_app()`) supporting hermetic in-memory or SQLAlchemy 2.0 relational SQLite/PostgreSQL repositories with automated baseline demo data seeding.
- **Standalone Packages**:
  - `packages/sdk`: Standalone `agentpay-sdk` providing typed Python client (`from agentpay_sdk import AgentPayClient`) using pure standard library HTTP transport.
  - `packages/protocol-types`: Standalone `agentpay-protocol-types` re-exporting external commerce protocols (ACP, AP2, UAP, x402).
  - `packages/schemas`: Standalone `agentpay-schemas` providing canonical JSON schemas for `ActionRequest` and API error envelopes.
  - `packages/test-utils`: Standalone `agentpay-test-utils` offering in-process client doubles and test fixtures.
- **Live Socket Network E2E Integration Suite**:
  - `apps/api/tests/unit/test_live_server_e2e.py` covering live TCP socket binding, `/api/v1/health`, `/api/v1/openapi.json`, `/api/v1/metrics`, CORS preflight, Python SDK catalog discovery, autonomous purchase execution, human approval threshold gating, emergency pause 503 fail-closed enforcement, and HMAC-SHA256 signed payment webhooks.
- **Supply Chain & Provenance**:
  - `scripts/generate_sbom.py`: CycloneDX 1.5 JSON SBOM and SLSA 1.2 build provenance attestation CLI with self-verification (`--verify`).
  - `scripts/verify_audit_chain.py`: Command-line verifier for SHA-256 chained audit ledgers.
- **Onboarding & Operations Documentation**:
  - `docs/onboarding/quickstart.md`: 5-minute zero-to-production quickstart guide covering setup, live server launch, outbox worker, Python SDK, human approval, and emergency kill switch operations.

### Changed
- Expanded verified test suite to **722 passing tests** (0 failed, 0 skipped).
- Expanded strict `mypy` type checking across all packages: `apps/api/src`, `apps/api/tests`, `apps/worker/src`, `infrastructure`, `scripts` (161 source files clean).
- Updated package version to `0.1.0` in `pyproject.toml` and registered console scripts.
- Added database persistence artifacts (`*.db`, `*.sqlite`, `*.sqlite3`) to `.gitignore`.

### Fixed
- Fixed static type errors in `apps/worker/src/agentpay_worker/worker.py` when accessing payment, reconciliation, and approval repository attributes.
- Resolved module resolution ambiguity in `scripts/` package structure.

### Security
- Empirically verified defense against all 14 mandatory attack vectors defined in Blueprint §177.
- Enforced strict least-privilege API client requiring explicit caller personas or bearer tokens, failing closed on unauthenticated or unconfigured requests.
- Enforced HMAC-SHA256 cryptographic webhook verification with timestamp tolerance window and replay locking.
- Guaranteed deterministic policy engine supremacy: AI models and LLM/JEV outputs sit strictly below the authorization boundary.

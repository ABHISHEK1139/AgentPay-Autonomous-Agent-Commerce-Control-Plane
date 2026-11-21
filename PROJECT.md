# Project: AgentPay Autonomous Agent Commerce Control Plane

## Architecture
AgentPay is an agent action-control and transaction platform enforcing deterministic spending policies, cryptographic human approval bindings, and fail-closed safety (§1, §2).
- **Domain Layer** (`apps/api/src/agentpay/domain/`): Pure Python business models (`Money`, `ActionRequest`, `Approval`, `Policy`, `Payments`, `Outbox`, `Security`). Zero framework, ORM, HTTP, or SDK imports (§10).
- **Application Layer** (`apps/api/src/agentpay/application/`): `PurchaseOrchestrator`, `ApprovalService`, `PaymentReconciler`, `AbuseDetector`, `EventBus`, domain ports.
- **Adapters & Infrastructure** (`apps/api/src/agentpay/adapters/`, `infrastructure/`, `security/`): `StripePaymentProvider`, `RazorpayPaymentProvider`, `ModelGateway`, Protocols (`ACP`, `AP2`, `UAP`, `x402`), Database repositories (in-memory & SQLAlchemy 2.0 SQLite), Resilience primitives (`CircuitBreaker`, `RetryPolicy`), Outbound SSRF guard.
- **API Layer** (`apps/api/src/agentpay/api/`): ASGI application `AgentPayApiApp`, `/api/v1/*`, `/agent/catalog`, `/api/v1/webhooks/payments`.
- **Worker Runtime** (`apps/worker/src/agentpay_worker/`, `apps/api/src/agentpay/workers/`): Background outbox dispatcher, periodic maintenance jobs.
- **Control Center Frontend** (`apps/web/`): React 19 + TypeScript 5 accessible dashboard with live ASGI client and offline fallback.
- **Adversarial & Verification Tooling** (`tests/adversarial/`, `scripts/`): Multi-protocol probes, concurrency probes, least-privilege client probes, webhook replay probes, SSRF resilience, model gateway resilience, SBOM/provenance verifier.

## Code Layout
- `apps/api/src/agentpay/api/`: ASGI application, HTTP request parsing, routing, security headers, CORS, error envelopes.
- `apps/api/src/agentpay/adapters/protocols/`: Protocol adapters (ACP, AP2, UAP, x402) for external agent commerce protocols.
- `apps/api/src/agentpay/security/`: SSRF URL validation, IP filtering, cryptographic hashing, redaction.
- `apps/api/src/agentpay/infrastructure/resilience/`: Circuit breaker, retry policies, backoff.
- `apps/api/src/agentpay/infrastructure/database/repositories/`: SQLAlchemy 2.0 repository implementations (commerce, cart, approval, order, payment, outbox, audit).
- `apps/api/src/agentpay/adapters/in_memory/`: Tenant-partitioned in-memory repository fallbacks.
- `apps/api/src/agentpay/application/use_cases/`: Core purchase flow orchestrator (`purchase_flow.py`).
- `apps/api/src/agentpay/application/services/`: Reconciliation, event bus, abuse detection.
- `apps/api/src/agentpay/workers/` and `apps/worker/src/agentpay_worker/`: Background worker runtime.
- `apps/web/`: React frontend Control Center, Vite configuration, index.html.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Model Gateway Concurrency & TOCTOU Fix | Atomic acquire/reserve and release in TokenBudgetTracker preventing concurrency overshoot | M1 | R1 (§23, §102) |
| 2 | Multi-Tenant Token Budget Isolation | Tenant-scoped agent key partitioning (tenant:X, agent:X:Y) and double-consume elimination | M1 | R1 (§49–§51) |
| 3 | Per-Tenant Circuit Breakers | ModelGateway tenant-isolated circuit breakers preventing cross-tenant denial-of-service | M1 | R1 (§103) |
| 4 | Model Gateway Resilience Probes | Update stress_test_model_gateway_resilience.py and unit tests to verify all 6 probes pass | M1 | R1 (§117) |
| 5 | SAGA Compensation Exception Rollback | PurchaseOrchestrator exception guards releasing inventory on create_payment/order_save failures | M2 | R2 (§43, §44) |
| 6 | SQL Repository Atomic Approval Consumption | SqlAlchemyApprovalRepository atomic_consume implementation with conditional row locking | M2 | R2 (§20) |
| 7 | PaymentReconciler Worker Contract Alignment | Add reconcile_timeout(attempt) to PaymentReconciler and fix frozen Approval status mutation in worker | M2 | R2 (§104, §105) |
| 8 | UAP Protocol Strict Integer Validation | Reject floats, booleans, negative amounts/quantities with MalformedPayloadError in UAP | M3 | R3 (§107, §108) |
| 9 | x402 Protocol Strict Integer Validation | Reject floats, booleans, negative amounts with MalformedPayloadError in x402 | M3 | R3 (§107, §108) |
| 10 | Protocol Stress Harness Expansion | Expand stress_test_protocols.py to include UAP and x402 adversarial probes | M3 | R3 (§107, §108) |
| 11 | Razorpay Provider Timestamp Hardening | Add timestamp tolerance verification to RazorpayPaymentProvider.verify_webhook_signature | M4 | R4 (§39, §61) |
| 12 | Webhook Replay & Reconciliation Hardening | End-to-end verification of HMAC verification, anti-replay, and payment state reconciliation | M4 | R4 (§41, §61) |
| 13 | Vite Dev Server Proxy Expansion | Add /agent proxy to apps/web/vite.config.ts alongside /api | M5 | R5 (§110, §111) |
| 14 | Frontend Live Dispatching & Offline Sync | Ensure Control Center live API dispatch, token/tenant propagation, and fallback contracts | M5 | R5 (§112, §121) |
| 15 | Full Release Verification & Documentation | Verify 4 CI gates + 9 auxiliary stress suites pass with 0 errors; update docs | M5 | R5 (§0.2, §176) |
| 16 | x402 Cryptographic Token Signing | Add HMAC-SHA256 signature to x402 settlement proof tokens and verify_settlement_token | M6 | Survey 1 (VULN-01) |
| 17 | Strict Content-Type Validation | Enforce Content-Type: application/json on mutating API endpoints (CORS preflight bypass protection) | M6 | Survey 1 (VULN-02) |
| 18 | Central HTTP Security Headers Injection | Inject CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy on all API responses | M6 | Survey 1 (VULN-08) |
| 19 | CORS Default Origin Hardening | Restrict server.py CORS default origins to localhost/loopback, eliminate wildcard (*) | M6 | Survey 1/3 (VULN-06) |
| 20 | Server Read Timeout & Slowloris Mitigation | Add socket read timeout and HTTP 408 handling in ThreadingHTTPServer handler | M6 | Survey 1 (VULN-05) |
| 21 | Webhook Error Sanitization & Observability Auth | Sanitize webhook 500 error envelopes; default require_observability_auth to True | M6 | Survey 1 (VULN-06, VULN-07) |
| 22 | Protocol Amount Validation Harmonization | Enforce price > 0 (reject <= 0 with MalformedPayloadError) across ACP, UAP, x402 adapters | M6 | Survey 1 (VULN-12) |
| 23 | SSRF DNS Rebinding TOCTOU Mitigation | Validate and pin IP address during outbound HTTP connection in SSRF filter and model provider | M6 | Survey 1/2 (VULN-03, VULN-09) |
| 24 | Circuit Breaker Half-Open Concurrency Limiter | Bound concurrent trial probes in HALF_OPEN state with dedicated active probe tracking | M6 | Survey 1 (VULN-10) |
| 25 | AbuseDetector API Integration | Wire AbuseDetector into _authenticate_request (credential stuffing) and /agent/catalog (scraping) | M6 | Survey 1 (VULN-11) |
| 26 | Emergency Controls Tenant Isolation | Strict scope enforcement in /api/v1/emergency/pause and resume (tenant admins cannot pause ALL/others) | M7 | Survey 2 (VULN-01) |
| 27 | Emergency Operator Identity Spoofing Fix | Reject request body operator_id mismatching cred.principal_id with 403 OPERATOR_IMPERSONATION_DENIED | M7 | Survey 1 (VULN-04) |
| 28 | Actor Identity Verification in API | Enforce agent-to-principal delegation binding in _check_actor_identity | M7 | Survey 1 (Obs-2.2) |
| 29 | TokenBudgetTracker Multi-Tenant Key Isolation | Eliminate cross-tenant key matching (k.endswith(:entity_id)); require explicit (tenant_id, agent_id) | M7 | Survey 2 (VULN-06) |
| 30 | Approval Repository Multi-Tenant Scoping | Make tenant_id mandatory in list_pending; ensure scoped lookups in SqlAlchemyApprovalRepository | M7 | Survey 2 (VULN-10) |
| 31 | Multi-Process Cart Checkout Concurrency Guard | Atomic conditional DB status transition (ACTIVE -> PROCESSING) preventing concurrent double-charges | M8 | Survey 2 (VULN-03) |
| 32 | Database Inventory Reservation Locking | Add with_for_update() row-level locking or atomic SQL conditional decrement in SqlAlchemyInventoryRepository | M8 | Survey 2 (VULN-02) |
| 33 | Daily Spend Concurrency Pre-Allocation | Pre-allocate spend before payment capture in purchase_flow to prevent concurrent limit bypass | M8 | Survey 2 (VULN-04) |
| 34 | SAGA Compensation for Multi-Item Cart Orders | Implement find_all_by_payment_attempt in OrderRepository and compensate all cart items on webhook failure | M8 | Survey 2 (VULN-05) |
| 35 | Audit Hash Chain Concurrency Guard | Exclusive tenant mutex/advisory lock during SqlAlchemyAuditRepository.record_event | M8 | Survey 2 (VULN-07) |
| 36 | Database Outbox Event Claiming Locking | Add with_for_update(skip_locked=True) in SqlAlchemyOutboxRepository.claim_pending | M8 | Survey 2 (VULN-08) |
| 37 | Product Search LIKE Wildcard DoS Mitigation | Escape %, _, and \ in query string in SqlAlchemyProductRepository.search | M8 | Survey 2 (VULN-12) |
| 38 | CLI Worker Outbox Repository Wiring | Wire SqlAlchemyOutboxRepository when database is configured in cli.py _handle_worker | M9 | Survey 3 (VULN-1) |
| 39 | Implement list_by_status on SqlAlchemyPaymentRepository | Query PaymentAttemptModel by status and tenant_id to enable stuck payment reconciliation in relational DB | M9 | Survey 3 (VULN-2) |
| 40 | Approval Expiry TOCTOU Race Condition Fix | Pass expected_status=ApprovalStatus.PENDING in worker approval expiry to prevent overwriting approvals | M9 | Survey 3 (VULN-3) |
| 41 | Worker Payment Reconciliation Backoff | Implement exponential backoff and retry cap for failed payment reconciliations in BackgroundWorker | M9 | Survey 3 (VULN-4) |
| 42 | EventBus Memory Leak Mitigation | Replace unbounded set in EventBus._processed_events with bounded OrderedDict/deque cache | M9 | Survey 3 (VULN-5) |
| 43 | Webhook Transient Replay Lockout Fix | Allow re-claiming webhook events that previously failed with transient error codes | M9 | Survey 1 (VULN-09) |
| 44 | Frontend Unpinned Tailwind CDN Remediation | Replace external Tailwind CDN with local PostCSS/Tailwind bundle or add SRI hash and strict CSP | M10 | Survey 3 (VULN-7) |
| 45 | Vite Dependency Vulnerability Upgrade | Update vite in apps/web/package.json to >=6.2.0 and resolve GHSA-8g5g-mxqh-grgj | M10 | Survey 3 (VULN-8) |
| 46 | Docker Compose Security Hardening | Bind Postgres and Redis ports to 127.0.0.1; configure Redis requirepass | M10 | Survey 3 (VULN-9) |
| 47 | CI/CD Pipeline Container Image Digest Pinning | Pin gitleaks container image in .github/workflows/ci.yml to immutable version tag/digest | M10 | Survey 3 (VULN-10) |
| 48 | Comprehensive Security & Regression Verification | Verify all 4 CI gates, 9 auxiliary stress suites, and newly added security test suites pass 100% | M10 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Component Bug Remediation & Model Gateway Resilience | Features 1–4 | none | DONE |
| M2 | Purchase Orchestrator & Saga Coordinator Hardening | Features 5–7 | none | DONE |
| M3 | Protocol Connectors & Adapters Hardening | Features 8–10 | none | DONE |
| M4 | Payment Gateway & Webhook Connectors | Features 11–12 | none | DONE |
| M5 | Frontend & Backend Contract Integration & Release Verification | Features 13–15 | M1..M4 | DONE |
| M6 | API, Protocols & Network Security Hardening | Features 16–25 | none | DONE |
| M7 | Multi-Tenant Isolation & Authorization Hardening | Features 26–30 | M6 | DONE |
| M8 | Database Concurrency, TOCTOU & SAGA Compensation | Features 31–37 | M7 | DONE |
| M9 | Worker Runtime, Webhook Replay & Resilience Hardening | Features 38–43 | M8 | DONE |
| M10 | Supply Chain, Web Frontend & Final Verification | Features 44–48 | M6..M9 | DONE |

## Interface Contracts

### M6: API, Protocols & Network Security
- `X402Adapter.from_execution_result(...) -> X402PaymentResponse`: generates cryptographically signed settlement token `x402_<claims_b64>.<hmac_hex>`.
- `X402Adapter.verify_settlement_token(token: str, secret: str) -> bool`: validates HMAC-SHA256 signature and unexpired timestamp.
- `AgentPayApiApp.__call__` / `server.py`: verifies `Content-Type: application/json` on mutating POST/PUT/PATCH requests, returns 415 `UNSUPPORTED_MEDIA_TYPE` on invalid content type.
- `AgentPayApiApp._inject_security_headers(headers: list[tuple[str, str]])`: centrally injects CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
- `server.py`: restricts default CORS `allowed_origins` to localhost/loopback, rejects wildcard `*`.
- `AgentPayHttpHandler`: sets `timeout = 10.0` and handles socket read timeouts with HTTP 408.
- `ACPAdapter`, `UAPAdapter`, `X402Adapter`: strictly enforce `price > 0`, raising `MalformedPayloadError` on `price <= 0`.
- `CircuitBreaker`: limits concurrent trial calls in `HALF_OPEN` state to `half_open_max_concurrent_probes` (default 1).
- `AgentPayApiApp`: instantiates `AbuseDetector` and records failed auth attempts and catalog queries.

### M7: Multi-Tenant Isolation & Authorization
- `POST /api/v1/emergency/pause` & `POST /api/v1/emergency/resume`: enforce `scope == f"tenant:{cred.tenant_id}"` for tenant admins; reject `ALL` or foreign tenant scopes with 403 `CROSS_TENANT_ACCESS_DENIED`. Verify `operator_id == cred.principal_id` (403 `OPERATOR_IMPERSONATION_DENIED` on mismatch).
- `AgentPayApiApp._check_actor_identity`: validates agent-to-principal delegation binding.
- `TokenBudgetTracker.get_usage(tenant_id: str, entity_id: str)`: requires explicit tenant ID, eliminating cross-tenant suffix aggregation.
- `ApprovalRepository.list_pending(tenant_id: str)`: requires mandatory `tenant_id`.

### M8: Database Concurrency, TOCTOU & SAGA Compensation
- `SqlAlchemyCartRepository.mark_processing(tenant_id: str, cart_id: str) -> bool`: atomically transitions `ACTIVE -> PROCESSING`, returning False if already processing/checked out.
- `SqlAlchemyInventoryRepository.reserve(tenant_id: str, variant_id: str, qty: int)`: uses `with_for_update()` row locking or atomic conditional decrement SQL.
- `PurchaseOrchestrator`: pre-allocates daily spend atomically before payment capture, rolling back reservation on payment failure.
- `OrderRepository.find_all_by_payment_attempt(tenant_id: str, attempt_id: str) -> list[Order]`: fetches all orders sharing a payment attempt ID.
- `PaymentReconciler`: compensates all orders and releases all reserved inventory items on webhook failure/refund.
- `SqlAlchemyAuditRepository.record_event`: acquires tenant-scoped mutex/advisory lock during hash computation and persistence.
- `SqlAlchemyOutboxRepository.claim_pending`: uses `with_for_update(skip_locked=True)` in polling queries.
- `SqlAlchemyProductRepository.search`: escapes `%` and `_` wildcards in search queries.

### M9: Worker Runtime & Webhook Replay
- `cli.py _handle_worker`: connects worker to `SqlAlchemyOutboxRepository` when database is configured.
- `SqlAlchemyPaymentRepository.list_by_status(status: PaymentStatus, tenant_id: str | None = None) -> list[PaymentAttempt]`: queries attempts by status.
- `BackgroundWorker._expire_stale_approvals`: passes `expected_status=ApprovalStatus.PENDING` to `approval_repo.save`.
- `BackgroundWorker._reconcile_stuck_payments`: applies exponential backoff and retry caps to failing reconciliations.
- `EventBus`: uses bounded collection (max 10,000 entries) for `_processed_events`.
- `InMemoryPaymentRepository` / `SqlAlchemyPaymentRepository`: allows re-claiming webhook events that previously failed transiently.

### M10: Supply Chain, Web Frontend & Release Verification
- `apps/web/index.html`: bundles Tailwind CSS locally or adds SRI hash and strict CSP.
- `apps/web/package.json`: updates Vite to `>=6.2.0`.
- `docker-compose.yml`: binds ports to `127.0.0.1`, sets Redis requirepass.
- `.github/workflows/ci.yml`: pins gitleaks to immutable tag/digest.
- Acceptance criteria: 100% pass across CI gates (`ruff`, `mypy`, `pytest`) and all adversarial stress test suites.

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

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Component Bug Remediation & Model Gateway Resilience | Features 1–4 | none | DONE |
| M2 | Purchase Orchestrator & Saga Coordinator Hardening | Features 5–7 | none | IN_PROGRESS |
| M3 | Protocol Connectors & Adapters Hardening | Features 8–10 | none | PLANNED |
| M4 | Payment Gateway & Webhook Connectors | Features 11–12 | none | PLANNED |
| M5 | Frontend & Backend Contract Integration & Release Verification | Features 13–15 | M1, M2, M3, M4 | PLANNED |

## Interface Contracts

### M1: Model Gateway ↔ TokenBudgetTracker & Resilience (VERIFIED)
- `TokenBudgetTracker.acquire(tenant_id: str, agent_id: str, estimated_tokens: int, estimated_cost: int) -> bool`
- `TokenBudgetTracker.release(tenant_id: str, agent_id: str, tokens: int, cost_minor_units: int) -> None`
- `TokenBudgetTracker.consume(tenant_id: str, agent_id: str, tokens: int, cost_minor_units: int) -> None`
- `TokenBudgetTracker.get_usage(entity_id: str) -> tuple[int, int]` (backward-compatible)
- `ModelGateway._get_circuit_breaker(tenant_id: str) -> CircuitBreaker`

### M2: Purchase Orchestrator ↔ Repositories & Reconciler
- `SqlAlchemyApprovalRepository.atomic_consume(tenant_id: str, approval_id: str) -> Approval`
- `PurchaseOrchestrator._execute_order_and_payment_pipeline()`: SAGA compensation `try...except` releasing inventory on payment/persistence errors
- `PaymentReconciler.reconcile_timeout(attempt: PaymentAttempt) -> PaymentAttempt`
- `Approval.reject(reason: str, rejected_at: datetime) -> Approval` (immutable return)

### M3: Protocol Adapters ↔ ActionRequest
- `UapAdapter.parse_payload(payload: dict[str, Any]) -> UapMessage` (raises `MalformedPayloadError` on `bool` or non-`int`)
- `X402Adapter.parse_payload(payload: dict[str, Any]) -> X402PaymentRequest` (raises `MalformedPayloadError` on `bool` or non-`int`)
- `tests/adversarial/stress_test_protocols.py`: executes ACP, AP2, UAP, and x402 adversarial suites

### M4: Payment Providers ↔ Webhooks
- `RazorpayPaymentProvider.verify_webhook_signature(payload: bytes, signature: str, secret: str, timestamp: int | None, tolerance_seconds: int = 300) -> bool`
- `PaymentReconciler.process_webhook(provider: str, payload: bytes, signature: str, timestamp_str: str | None)`

### M5: Web Frontend ↔ ASGI Backend
- `apps/web/vite.config.ts`: proxy `"/api"` and `"/agent"` to `http://localhost:8000`
- `apps/web/src/services/api.ts`: Bearer token authorization, `X-Tenant-ID` propagation, offline fallback

# Project: AgentPay Hardening & Production Extensions

## Architecture
AgentPay is an agent action-control and transaction platform built as a modular monolith with strict Clean Architecture dependency boundaries (§10).
- **Domain Layer** (`apps/api/src/agentpay/domain/`): Pure Python business models, domain events, and state machines. Zero framework, ORM, HTTP, or SDK imports.
- **Application Layer** (`apps/api/src/agentpay/application/`): Use cases, orchestrators, ports, and domain services.
- **Infrastructure & Adapters** (`apps/api/src/agentpay/infrastructure/`, `adapters/`, `security/`): Implementations of application ports including database repositories (in-memory & SQLAlchemy 2.0 SQLite), resilience primitives, and observability collectors.
- **API Layer** (`apps/api/src/agentpay/api/`): HTTP routes, error envelopes, and middleware.
- **Worker Runtime** (`apps/worker/src/agentpay/worker/`): Background outbox event dispatcher, periodic maintenance jobs (approval expiration, payment reconciliation).
- **Control Center Frontend** (`apps/web/`): React 19 + TypeScript 5 accessible dashboard.
- **Scripts & Security** (`scripts/`, `security/`): Standalone CLI tools (audit verifier, SBOM & provenance generator).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | SSRF Protection | Outbound URL validator blocking private IPs, metadata, loopback, IPv6 mapped/ULA | M1 | §60 |
| 2 | Retries & Backoff | Bounded exponential backoff with full jitter, read-only/idempotent restriction | M1 | §102 |
| 3 | Circuit Breaker | Thread-safe state machine (CLOSED/OPEN/HALF_OPEN) with failure thresholds | M1 | §103 |
| 4 | Model Gateway | DecisionEngine adapter with token budget, timeout, circuit breaker, fail-closed fallback | M1 | §23 |
| 5 | Evaluation Dataset & Runner | Versioned JSON benchmark dataset and automated evaluator in pytest | M1 | §117 |
| 6 | Transactional Outbox | OutboxEvent domain entity, OutboxRepository port, in-memory & SQLAlchemy implementations | M2 | §106 |
| 7 | Event Bus | Pluggable EventBus interface and thread-safe in-process InMemoryEventBus | M2 | §104 |
| 8 | Background Worker Runtime | Functional runtime in apps/worker/ polling outbox, dispatching events with idempotency | M2 | §105 |
| 9 | Maintenance Jobs | Periodic ApprovalSweeperJob and PaymentReconciliationJob | M2 | §104, §105 |
| 10 | Security Events | SecurityEvent model and AuditEventType additions for audit ledger | M3 | §62, §63 |
| 11 | Abuse Detection Service | AbuseDetector monitoring credential stuffing, scraping, tool loops, spend velocity | M3 | §62, §63 |
| 12 | Observability Tracer & Meter | OpenTelemetry-compatible Tracer (Span, span_id) and Meter (Counter, Histogram) | M3 | §47 |
| 13 | Metrics Endpoint | Authenticated /api/v1/metrics route returning Prometheus text and JSON | M3 | §47 |
| 14 | Frontend Accessibility | ARIA landmarks, roles, live regions, keyboard navigation across apps/web/src/ | M4 | §121 |
| 15 | Web Accessibility Tests | Programmatic assertions in apps/web/tests/ (node --test tests/web.test.mjs) | M4 | §121 |
| 16 | SBOM & Provenance CLI | scripts/generate_sbom.py parsing uv.lock & package.json into CycloneDX 1.5 & SLSA | M4 | §57, §130 |
| 17 | SBOM Unit Tests | Comprehensive automated tests for SBOM & SLSA provenance generation | M4 | §57, §130 |
| 18 | Documentation & Gate Sync | Synchronize test/file counts and status in AGENTS.md, README.md, docs/ | M5 | §0.1, §176 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Outbound SSRF & Resilience & Model Gateway | Features 1–5 | none | COMPLETE |
| M2 | Transactional Outbox, Event Bus & Workers | Features 6–9 | none | COMPLETE |
| M3 | Abuse Detection & Structured Observability | Features 10–13 | M1 (observability hooks) | COMPLETE |
| M4 | Frontend Accessibility & Supply-Chain SBOM | Features 14–17 | none | COMPLETE |
| M5 | Documentation Sync & Release Verification | Feature 18 | M1, M2, M3, M4 | COMPLETE |

## Interface Contracts

### M1: Security SSRF & Resilience ↔ Applications
- `OutboundUrlValidator.validate_url(url: str, allowed_schemes: set[str] | None, allowed_hosts: set[str] | None) -> SafeTarget`
- `CircuitBreaker.call(func: Callable[..., T], *args, **kwargs) -> T` (raises `CircuitBreakerOpenError`)
- `RetryPolicy.execute(func: Callable[..., T], *args, **kwargs) -> T`
- `ModelGateway(DecisionEngine).evaluate(context: DecisionContext) -> Decision` (fail-closed fallback returning `DecisionRecommendation.REVIEW` / `DENY`, `RiskLevel.HIGH`, `confidence=0.0`)

### M2: Transactional Outbox & Event Bus ↔ Worker
- `OutboxRepository.save(event: OutboxEvent) -> None`
- `OutboxRepository.list_pending(tenant_id: str, limit: int) -> list[OutboxEvent]`
- `OutboxRepository.mark_published(tenant_id: str, event_id: str, published_at: datetime) -> None`
- `OutboxRepository.mark_failed(tenant_id: str, event_id: str, error: str, retry_count: int, next_retry_at: datetime | None) -> None`
- `EventBus.publish(event: OutboxEvent) -> None`
- `EventBus.subscribe(pattern: str, handler: Callable[[OutboxEvent], None]) -> None`
- `ApprovalRepository.list_pending(tenant_id: str, before: datetime) -> list[Approval]`
- `PaymentRepository.list_by_status(tenant_id: str, status: PaymentStatus) -> list[PaymentAttempt]`

### M3: Abuse Detector & Observability ↔ Domain & API
- `AbuseDetector.record_auth_failure(tenant_id: str, principal_id: str, ip: str) -> None`
- `AbuseDetector.record_request(tenant_id: str, agent_id: str, path: str) -> None`
- `AbuseDetector.record_spend(tenant_id: str, agent_id: str, amount: Money) -> None`
- Emits `AuditEventType.SECURITY_EVENT_EMITTED`, transitions agent to `QUARANTINED` / `SUSPENDED`.
- `Tracer.start_span(name: str, attributes: dict[str, Any] | None) -> Span`
- `Meter.counter(name: str).increment(delta: int, labels: dict[str, str]) -> None`
- `Meter.histogram(name: str).record(value: float, labels: dict[str, str]) -> None`

### M4: Accessibility & SBOM ↔ Web / Build
- ARIA semantic roles: `role="dialog"`, `role="alert"`, `role="tablist"`, `role="tab"`
- Keyboard handling: `onKeyDown` (`Enter`, `Space`, `Escape`)
- `scripts/generate_sbom.py --format cyclonedx|slsa --output <path>`

## Code Layout
- `apps/api/src/agentpay/domain/` (Pure domain entities: `security.py`, `outbox.py`, etc.)
- `apps/api/src/agentpay/application/ports/` (`outbox_port.py`, `event_bus_port.py`, etc.)
- `apps/api/src/agentpay/application/services/` (`abuse_detector.py`, etc.)
- `apps/api/src/agentpay/infrastructure/resilience/` (`circuit_breaker.py`, `retry.py`)
- `apps/api/src/agentpay/infrastructure/observability/` (`tracer.py`, `meter.py`)
- `apps/api/src/agentpay/infrastructure/database/` (SQLAlchemy models and repositories)
- `apps/api/src/agentpay/security/` (`ssrf.py`)
- `apps/api/src/agentpay/adapters/model_gateway/` (`model_gateway.py`)
- `apps/worker/src/agentpay/worker/` (`runtime.py`, `dispatcher.py`, `jobs.py`)
- `apps/web/src/` (React accessibility upgrades)
- `scripts/` (`generate_sbom.py`)
- `tests/` (`apps/api/tests/unit/`, `tests/fixtures/evaluation/`, `apps/web/tests/`)

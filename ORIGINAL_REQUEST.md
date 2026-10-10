# Original User Request

## Initial Request — 2026-10-06T14:36:45Z

AgentPay is an autonomous agent commerce control plane that governs AI agent financial actions through strict identity, policy, approval, payment sandboxing, and immutable auditing. Deliver the full MVP vertical slice (§74) connecting human intent, agent discovery, evidence verification, policy/approval gating, payment execution, and audit.

Working directory: c:/Users/ak612/OneDrive/Desktop/platfrom
Integrity mode: development

## Requirements

### R1. Identity, Tenancy & Agent Authorization
Implement multi-tenant isolation, principal identity, agent registration with explicit audiences and capabilities, and deterministic ABAC/RBAC authorization checks. Ensure cross-tenant data access is strictly blocked at the boundary and agents cannot alter their own permission grants.

### R2. Commerce Catalog, Evidence & Conflict Detection
Implement a domain-neutral commerce abstraction supporting product discovery, inventory checks, structured evidence tracking with data provenance (price, stock, specifications), and deterministic conflict detection (price mismatch, inventory mismatch) that fails closed before any financial commitment.

### R3. Deterministic Policy & Human Approval Engine
Connect the canonical `ActionRequest` pipeline to the deterministic spending policy engine, supporting multi-tier approval workflows (automatic within limits, human review above thresholds, hard denial above caps). Decision intelligence (JEV / mock AI) may score risk and recommend actions, but the deterministic policy engine remains the authoritative gatekeeper.

### R4. Payment Orchestration, Idempotency & Saga Compensation
Implement payment abstraction with a sandbox/mock provider (Razorpay sandbox / MockPaymentProvider) supporting the complete payment state machine (`CREATED`, `AUTHORIZED`, `CAPTURED`, `FAILED`, `UNKNOWN`, `REFUNDED`). Ensure financial operations are strictly idempotent with compensation handling for timeouts and reconciliation without duplicate charges.

### R5. Immutable Audit Ledger & Traceability
Implement an audit logging system that records structured, tamper-evident audit events for every critical lifecycle transition (intent parsed, evidence retrieved, policy evaluated, approval requested/granted, payment attempted, order created). Decisions must store auditable factors and evidence references without private internal chain-of-thought.

## Acceptance Criteria

### Core Capabilities & Scenarios
- [ ] **Autonomous purchase flow**: Under-limit purchase evaluates to `ALLOW`, executes through sandbox payment, confirms the order, and writes the audit trail.
- [ ] **Approval-gated flow**: Over-autonomous-limit purchase evaluates to `REVIEW`, transitions to awaiting approval, executes only upon explicit approval binding, and fails if modified.
- [ ] **Hard denial flow**: Request exceeding hard spending ceiling evaluates to `DENY` and terminates without side effects.
- [ ] **Evidence mismatch protection**: Price or inventory discrepancies between agent assertion and merchant evidence evaluate to a mismatch conflict and abort execution before payment.
- [ ] **Payment idempotency & timeout recovery**: Replaying an identical payment request returns cached results without duplicate charges; simulated timeout reconciles state rather than blindly retrying charges.
- [ ] **Tenant isolation**: Cross-tenant entity access attempts fail closed with explicit authorization rejection.
- [ ] **Privilege escalation defense**: Agent attempts to expand its own spending limits or bypass policy are strictly rejected.

### Architecture & Quality Gates
- [ ] Domain layer preserves purity: zero imports of web frameworks, ORMs, or third-party SDKs, verified by boundary test (`test_architecture_boundaries.py`).
- [ ] All CI verification gates pass cleanly without errors:
  - `uv run ruff format --check .`
  - `uv run ruff check .`
  - `uv run mypy`
  - `uv run pytest`
- [ ] The default test suite runs self-contained without requiring live external network services or running Docker daemons.


## Follow-up — 2026-10-06T18:18:46Z

# Teamwork Project — AgentPay Autonomous Agent Commerce Control Plane (Sprint 10 & 11)

> Status: Launched
> Goal: Build Sprint 10 Control Center (Live Transaction Viewer & Approval Dashboard) & Sprint 11 Protocol Adapters
> Requested team: Lead Orchestrator with teamwork_preview

AgentPay is an agent action-control and transaction platform enabling AI agents to transact safely on behalf of humans while enforcing identity, deterministic spending policy, evidence verification, and human-in-the-loop approval (§1, §2).

Working directory: c:\Users\ak612\OneDrive\Desktop\platfrom
Integrity mode: development

## Requirements

### R1. Live Transaction Viewer & Dashboard (§110, §111, §112)
Build the Control Center frontend in `apps/web/` visualizing the 10-step inspectable pipeline:
`USER INTENT → AGENT IDENTITY → PRODUCT DISCOVERY → EVIDENCE → RECOMMENDATION → JEV → POLICY → APPROVAL → PAYMENT → ORDER`
Provide demonstration views for Scenarios 1–8 (§112), including human approval review screen (§66) and emergency kill switch controls (§21, §67).

### R2. Agent Protocol Adapter Layer (§107, §108)
Implement protocol adapters translating ACP (Agent Commerce Protocol), AP2, UAP, and x402 requests into the canonical `ActionRequest` domain model without leaking protocol-specific objects into the domain core.

### R3. Quality & Verification Gates
All changes must pass the 4 mandatory CI gates:
1. `uv run ruff format --check .`
2. `uv run ruff check .`
3. `uv run mypy`
4. `uv run pytest`

## Acceptance Criteria

### Control Center & Visualizer
- [ ] Inspectable 10-step visualization renders all lifecycle phases from Intent to Order.
- [ ] Emergency kill switch UI button immediately halts autonomous payment actions.
- [ ] Human approval screen displays who, what, why, amount, merchant, evidence, and risk factors (§66).

### Protocol Adapters
- [ ] ACP and AP2 adapter prototypes map external messages to `ActionRequest`.
- [ ] Domain layer purity is preserved (§10) with 0 forbidden imports.
- [ ] 100% of unit, integration, and security tests pass.


## Follow-up — 2026-10-07T17:08:48Z

# Teamwork Project — AgentPay Autonomous Agent Commerce Control Plane (Sprint 12)

> **Status:** Launched
> **Goal:** Sprint 12 Release Hardening, Final Autonomous Audit (§177), and Final Release Certification (§176)
> **Requested team:** Full team (Lead Orchestrator with teamwork_preview)

AgentPay is an agent action-control and transaction platform enabling AI agents to transact safely on behalf of humans while enforcing identity, deterministic spending policy, evidence verification, and human-in-the-loop approval (§1, §2). Sprints 0–11 are built and passing (304 unit/integration tests, 4 CI gates clean, web control center compiled). This sprint executes Sprint 12 Release Hardening and the Section 177 Final Autonomous Audit.

Working directory: c:\Users\ak612\OneDrive\Desktop\platfrom
Integrity mode: development

## Requirements

### R1. Final Autonomous Audit & Threat Verification (§177)
Perform an exhaustive adversarial audit from the perspective *"I do not trust this system"* against all 14 mandatory attack vectors:
1. Authorization bypass (§2.1, §18)
2. Payment duplication / replay (§42, §43)
3. Tenant leakage (§49–§51)
4. Agent impersonation / status bypass (§14, §15)
5. Policy bypass via advisory AI (§2.2, §134)
6. Prompt injection in external text (§25)
7. Tool abuse / arbitrary function calls (§26, §27)
8. Evidence manipulation / price discrepancy (§32, §33)
9. Merchant manipulation / status bypass (§30, §37)
10. Replay attacks on human approval bindings (§20)
11. Race conditions on inventory reservation (§30, §44)
12. Stale catalog data cache exploitation (§30)
13. Silent failure / unhandled error propagation (§2.4)
14. Tamper gaps in SHA-256 audit ledger (§29, §45)

### R2. Operations, Runbooks & Production Hardening (§176, §2907–§2919)
Deliver release documentation and operational controls:
- Incident response runbook with emergency kill switch invocation protocols (`docs/operations/incident-runbook.md`, §21, §67).
- Backup and disaster recovery procedures with audit hash chain verification (`docs/operations/backup-restore.md`, §45).
- Autonomous audit verification report (`docs/security/autonomous-audit-report.md`, §176, §177).
- Architecture and repository current-state synchronization.

### R3. Quality & Verification Gates (§0.2, §77, §176)
Execute full test validation across the entire repository:
1. `uv run ruff format --check .` (0 formatting violations)
2. `uv run ruff check .` (0 lint violations)
3. `uv run mypy` (0 type errors across all source files)
4. `uv run pytest` (100% of unit, boundary, adversarial, and E2E tests pass)
5. Web Control Center test verification (`node --test tests/web.test.mjs`)

## Acceptance Criteria

### Security & Autonomous Audit (§177)
- [ ] Automated tests explicitly verify failure closed on all 14 attack vectors.
- [ ] Domain layer purity (§10) preserved with zero third-party/framework imports.
- [ ] Integer minor units strictly enforced with float and boolean amounts rejected.

### Operations & Documentation (§176)
- [ ] Incident response runbook, backup/recovery procedure, and audit report published in `docs/`.
- [ ] Emergency kill switch verified to block all autonomous actions cluster-wide with HTTP 503.

### Release Gates
- [ ] All 4 CI gates pass cleanly with exit code 0.
- [ ] Web dashboard tests pass with 0 failures.


## Follow-up — 2026-10-10T06:40:03Z

# Teamwork Project — AgentPay Blueprint Gaps Closure & Production Hardening

> **Status:** Launched
> **Goal:** Close critical architectural gaps identified in §§1–183: §14 Authentication & Credential Binding, §26/§27 Fixed Tool Registry with Risk Tiers, §62 Rate Limiting, §47 Observability Trace IDs, §29/§118 Decision Metadata, §133 Operational Runbooks, and §53 ASVS Mapping.
> **Requested team:** Full team (Lead Orchestrator with teamwork_preview)

AgentPay is an agent action-control and transaction platform enabling AI agents to transact safely on behalf of humans while enforcing identity, deterministic spending policy, evidence verification, and human-in-the-loop approval (§1, §2). 

This project implements the unbuilt architectural, security, and operational surfaces identified in the exhaustive blueprint audit of `AGENTS.md` §§1–183 to transform the current prototype slice into a cryptographically bound, authenticated, and rate-limited production platform.

Working directory: c:\Users\ak612\OneDrive\Desktop\platfrom
Integrity mode: development

---

## Requirements

### R1. Authentication & Principal-to-Tenant Binding (§14, §15, §16, §67)
Implement real authentication for the HTTP API and domain layer:
- Define `Credential` domain model (API key, Bearer token, HMAC signature) bound to `tenant_id` and `principal_id` (user or agent) with status and expiration tracking.
- Create `CredentialRepository` port and in-memory adapter.
- Enforce authentication on all API routes: incoming callers must present valid credentials (`Authorization: Bearer <token>` or `X-API-Key: <key>`). Rejection with HTTP 401 `UNAUTHENTICATED` when credentials are missing or invalid.
- Cryptographically verify that asserted `X-Tenant-ID` matches the authenticated credential's `tenant_id`; reject mismatches with HTTP 403 `CROSS_TENANT_ACCESS_DENIED`.
- Enforce operator/administrator authentication on emergency kill switch routes (`/api/v1/emergency/*`), rejecting unauthorized callers (§67).

### R2. Tool Registry & Risk Classification (§26, §27)
Implement a fixed, permissioned tool registry:
- Define `ToolRiskTier` (`READ_ONLY`, `FINANCIAL_MUTATING`, `ADMINISTRATIVE`, `SYSTEM`) and `ToolDefinition` (tool name, description, risk tier, required capability, enabled status).
- Enforce tool execution gating in domain/application orchestrator: agents may only invoke tools that exist in the registry, are enabled, and match the agent's granted capabilities.
- Unknown tools or tool calls exceeding agent privileges must fail closed with typed errors (`UnknownToolError`, `UnauthorizedToolAccessError`).

### R3. API Rate Limiting (§52, §62)
Implement defensive rate limiting to prevent denial-of-service and brute-force financial replay attacks:
- Enforce per-tenant and per-agent request throttling (token bucket or sliding window) across all mutating financial endpoints.
- When thresholds are exceeded, fail closed with HTTP 429 `RATE_LIMIT_EXCEEDED` and standard RFC error envelopes including retry guidance.

### R4. Observability & Distributed Trace Correlation (§47)
Implement end-to-end tracing and correlation:
- Inject and propagate `trace_id` alongside `request_id` and `correlation_id` across the HTTP middleware, orchestrator, and domain audit events.
- Surface `trace_id` in audit ledger preimages and API error/success responses to support end-to-end forensic traceability.

### R5. Model Decision Metadata Enrichment (§29, §118)
Enrich advisory AI decision metadata:
- Add `prompt_version`, `tool_policy_version`, and `policy_version` to the `Decision` domain model.
- Preserve backward compatibility while ensuring all JEV/model audit records capture exact model evaluation provenance without storing chain-of-thought text.

### R6. Operational Runbooks & ASVS Security Documentation (§53, §87, §133)
Deliver missing operational runbooks and engineering journals:
- Author the missing named operational runbooks in `docs/runbooks/`:
  - `01-agent-revocation-runbook.md`
  - `02-emergency-kill-switch-runbook.md`
  - `03-payment-timeout-reconciliation-runbook.md`
  - `04-audit-chain-tamper-recovery-runbook.md`
  - `05-merchant-suspension-runbook.md`
  - `06-cross-tenant-incident-runbook.md`
  - `07-rate-limit-abuse-mitigation-runbook.md`
  - `08-key-rotation-and-credential-revocation-runbook.md`
  - `09-disaster-recovery-failover-runbook.md`
- Create `docs/engineering/experiments.md` (§87).
- Create OWASP ASVS v4.0 verification mapping in `security/asvs/asvs-v4-mapping.md` (§53).

---

## Acceptance Criteria

### Authentication & Authorization (§14, §15, §16, §67)
- [ ] Requests without valid credentials receive HTTP 401 `UNAUTHENTICATED`.
- [ ] Requests attempting to access Tenant B with a Tenant A credential receive HTTP 403 `CROSS_TENANT_ACCESS_DENIED`.
- [ ] Emergency kill switch endpoints reject requests lacking operator/admin credentials.
- [ ] Domain layer purity (§10) is preserved with zero framework imports in domain models.

### Tool Security & Rate Limiting (§26, §27, §62)
- [ ] Fixed `ToolRegistry` rejects unregistered tool names and unpermitted capability calls.
- [ ] High-volume bursting on checkout/mandate endpoints triggers HTTP 429 `RATE_LIMIT_EXCEEDED`.

### Observability & Traceability (§47, §118)
- [ ] Every emitted `AuditEvent` and API response includes a verifiable `trace_id`.
- [ ] `Decision` objects capture `model_version`, `prompt_version`, `tool_policy_version`, and `policy_version`.

### Documentation & Runbooks (§53, §87, §133)
- [ ] All 9 named operational runbooks exist in `docs/runbooks/`.
- [ ] `experiments.md` and ASVS v4.0 verification matrix are documented and committed.

### Quality & Verification Gates
- [ ] `uv run ruff format --check .` passes with 0 violations.
- [ ] `uv run ruff check .` passes with 0 lint errors.
- [ ] `uv run mypy` passes cleanly across all source files.
- [ ] `uv run pytest` passes 100% of tests (all existing tests + new authentication, tool registry, and rate-limiting test suites).


## Follow-up — 2026-10-10T11:26:20Z

Server restarted. Please resume work on the remaining deliverables: R2 (Tool Registry integration and unit tests), R3 (Rate Limiting integration in API endpoints and unit tests), R4 (trace_id correlation across app and audit events), and R6 (the 9 named operational runbooks in docs/runbooks/ and ASVS mapping). All gates currently green with 424 passed tests. Continue and finish the plan.


## Follow-up — 2026-10-10T16:56:02Z

# Teamwork Project — AgentPay Production Hardening & Core Architecture (Sprint 14)

> **Status:** Launched
> **Goal:** Comprehensive platform hardening across test harness security, ISO 4217 multi-currency exponent engine (TD-002), payment webhook ingestion with HMAC replay defense (§61), and persistent database architecture (§49).
> **Requested team:** Full team (Lead Orchestrator with teamwork_preview)

AgentPay is an autonomous agent commerce control plane enforcing deterministic spending policies, cryptographic human approval bindings, and fail-closed safety. This milestone hardens the test harness against silent privilege escalation, resolves multi-currency math (TD-002), introduces cryptographically verified payment webhook ingestion (§61), and establishes persistent storage architecture (§49).

Working directory: c:\Users\ak612\OneDrive\Desktop\platfrom
Integrity mode: development

---

## Requirements

### R1. Test Harness Hardening & Least-Privilege Test Client
Tighten `ApiTestClient` in `apps/api/src/agentpay/api/app.py`:
- Eliminate silent `ADMIN` token injection. Change `default_role` to least-privilege `UserRole.BUYER`.
- Add explicit, fluent client helpers:
  - `client.as_admin(tenant_id)`: injects valid administrator credentials.
  - `client.as_agent(agent_id, tenant_id)`: injects valid agent credentials.
  - `client.as_unauthenticated()`: explicitly disables credential injection.
- Ensure negative security tests and emergency kill-switch tests explicitly state their authentication context and cannot pass vacuously.

### R2. ISO 4217 Currency Exponent Engine & Multi-Currency Safety (TD-002)
Resolve technical debt TD-002:
- Implement an ISO 4217 currency registry mapping standard 3-letter currency codes to their minor-unit exponents (e.g. `JPY`: 0, `INR`/`USD`/`EUR`: 2, `KWD`/`BHD`: 3).
- Enhance `Money` validation and formatting so zero-decimal and 3-decimal currencies are handled safely without binary floating-point drift.
- Ensure cross-currency calculations strictly fail closed unless explicit exchange rates are provided.

### R3. Payment Webhook Ingestion & Cryptographic Replay Protection (§61)
Implement incoming payment webhook handling:
- Add HTTP endpoint `POST /api/v1/webhooks/payments` supporting provider event callbacks.
- Enforce HMAC-SHA256 signature verification against configured webhook secrets (`X-Webhook-Signature`).
- Enforce timestamp freshness (reject events older than 300 seconds) and replay protection via idempotency store.
- Reconcile asynchronous payment transitions (`payment.captured`, `payment.failed`, `payment.refunded`) into the domain payment state machine and update orders accordingly.

### R4. Persistent Storage Architecture & Database Foundation (§49)
Establish the relational database persistence layer:
- Define SQLAlchemy / SQLModel schema definitions in `infrastructure/database/` mirroring domain entities (`Tenant`, `Agent`, `Product`, `Order`, `Payment`, `AuditEvent`, `Credential`).
- Ensure schema enforces tenant isolation and foreign key integrity.
- Maintain seamless dual-mode capability: retain in-memory adapters for hermetic unit testing, while providing database-backed repository implementations for staging/production.

---

## Acceptance Criteria

### Test Harness Security (R1)
- [ ] `ApiTestClient` defaults to `BUYER` role and rejects unprivileged access to `/api/v1/emergency/*` with HTTP 403 `UNAUTHORIZED_OPERATOR`.
- [ ] Fluent helpers (`.as_admin()`, `.as_unauthenticated()`, `.as_agent()`) work seamlessly across all test suites.
- [ ] 100% of existing and new test suites pass with zero false positives.

### Currency Engine (R2)
- [ ] ISO 4217 currency exponent table correctly resolves exponents for JPY (0), USD (2), KWD (3).
- [ ] Zero floating-point arithmetic is used; minor units are strictly validated as integers according to exponent rules.
- [ ] Unit tests verify proper handling and rejection of currency mismatches.

### Webhook Security (R3)
- [ ] Webhook requests with invalid or missing HMAC signatures are rejected with HTTP 401 `INVALID_WEBHOOK_SIGNATURE`.
- [ ] Replayed webhook payloads or expired timestamps are rejected.
- [ ] Valid webhooks cleanly transition payment and order states and record tamper-evident audit events.

### Persistence Foundation (R4)
- [ ] Relational schemas in `infrastructure/database/` cleanly map all core domain models without polluting domain purity (§10).
- [ ] AST boundary tests verify zero forbidden database imports in `domain/`.

### Quality & Verification Gates
- [ ] `uv run ruff format --check .` passes with 0 violations.
- [ ] `uv run ruff check .` passes with 0 lint errors.
- [ ] `uv run mypy` passes cleanly across all source files.
- [ ] `uv run pytest` passes 100% of tests.
- [ ] Auxiliary suites (`node --test tests/web.test.mjs`, protocol stress tests, concurrency stress tests) pass cleanly.


## Follow-up — 2026-10-10T19:07:47Z

Complete the remaining unbuilt and partial architectural, security, resilience, asynchronous worker/outbox, observability, frontend accessibility, and supply-chain provenance requirements from `AGENTS.md` for the AgentPay Autonomous Agent Commerce Control Plane.

Working directory: c:\Users\ak612\OneDrive\Desktop\platfrom
Integrity mode: development

## Verification Resources & Environment Constraints
- **Authoritative Specification**: `AGENTS.md` and `docs/engineering/known-limitations.md`.
- **Controlled Environment**: Windows PowerShell 5.1 (`uv` 0.12.22, Python 3.12.10, Node v22.14.0). The local Docker daemon is not running (`docker info` fails); all automated tests must execute hermetically in-process using in-memory adapters and in-memory SQLite without making live external network connections.
- **Architectural Boundary (§10)**: `apps/api/src/agentpay/domain/` must remain 100% pure Python with zero framework, ORM, HTTP, or SDK imports (enforced by `apps/api/tests/unit/test_architecture_boundaries.py`).
- **Verification Gates**:
  - `uv run ruff format --check .` (`extend-exclude = ["*.md"]` must remain intact)
  - `uv run ruff check .`
  - `uv run mypy`
  - `uv run pytest`
  - `node --test tests/web.test.mjs`

---

## Requirements

### R1. Outbound SSRF Protection, Resilience Primitives & Model Gateway (§23, §60, §102, §103, §117)
- **SSRF Protection (§60)**: Implement an outbound URL/target validator that enforces HTTPS scheme allowlists, host allowlists, and blocks loopback (`127.0.0.0/8`, `::1`), RFC 1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local/cloud metadata endpoints (`169.254.0.0/16`, `fe80::/10`), and multicast/unspecified addresses, rejecting unsafe targets before any request is dispatched.
- **Retries & Circuit Breakers (§102, §103)**: Implement a bounded retry policy with exponential backoff and jitter (restricted to idempotent or read-only operations; never blindly retrying unverified payment charges) and a thread-safe state-machine Circuit Breaker (`CLOSED` → `OPEN` → `HALF_OPEN` → `CLOSED`) with configurable failure thresholds and recovery timeouts.
- **Model Gateway & Versioned Evaluation Dataset (§23, §117)**: Implement a `ModelGateway` adapter implementing `DecisionEngine` that enforces token/cost budgets, request timeouts, circuit-breaker protection, and deterministic fail-closed fallback (`REVIEW`/`DENY` when the model fails or times out). Pair it with a versioned benchmark evaluation dataset and automated evaluator verifying advisory decision quality, prompt-injection resistance, and budget compliance (§117).

### R2. Background Workers, Event Bus & Transactional Outbox (§104, §105, §106)
- **Transactional Outbox (§106)**: Implement `OutboxEvent` domain/persistence models (both in-memory and SQLAlchemy 2.0 relational repositories) so state mutations and domain events are persisted atomically before asynchronous publication.
- **Event Bus & Background Worker (§104, §105)**: Implement an in-process/pluggable `EventBus` subscriber registry and a functional worker runtime under `apps/worker/src/` (replacing the placeholder README-only state) capable of polling and dispatching pending outbox events with idempotency, sweeping expired approvals into `EXPIRED` state, and reconciling stuck `UNKNOWN` payment attempts via `PaymentReconciler`.

### R3. Abuse Detection, Security Events & Structured Observability (§47, §62, §63)
- **Security Events & Abuse Detection (§62, §63)**: Implement `SecurityEvent` and an `AbuseDetector` service that monitors and detects credential stuffing (repeated authentication failures), catalog scraping bursts, rapid tool-loop exhaustion, and anomalous spend velocity spikes — emitting tamper-evident `SecurityEvent` records to the audit ledger and automatically quarantining or suspending rogue agents when thresholds are breached.
- **Observability Spans & Metrics (§47)**: Implement a zero-dependency OpenTelemetry-compatible telemetry collector (`Tracer` / `Meter`) that propagates `trace_id` and `span_id` across API, orchestrator, policy, payment, and webhook execution paths, and records queryable counters and latency histograms (e.g., policy decisions by outcome, payment attempts by status, webhook signature failures, circuit breaker transitions), exposed via an authenticated `/api/v1/metrics` or telemetry inspection endpoint.

### R4. Frontend Accessibility (§121) & Supply-Chain SBOM / Provenance (§57, §130)
- **Control Center Accessibility (§121)**: Upgrade the React/TypeScript Control Center (`apps/web/src/`) with semantic ARIA attributes (`role`, `aria-label`, `aria-live`, `aria-expanded`, `aria-pressed`), live status announcements for approval requests and emergency kill-switch state changes, and keyboard navigation handlers (`Enter`, `Space`, `Escape`, arrow/tab focus management). Expand `apps/web/tests/` (`node --test tests/web.test.mjs`) to programmatically verify accessibility contracts and scenario integrity.
- **SBOM & Build Provenance (§57, §130)**: Implement a deterministic CLI tool (`scripts/generate_sbom.py`) and supporting verification tests that parse `pyproject.toml`, `uv.lock`, and `package.json` to emit a valid CycloneDX 1.5 JSON SBOM and a SLSA-aligned build provenance attestation containing SHA-256 artifact digests.

---

## Acceptance Criteria

### Security, Resilience & Model Gateway (R1)
- [x] Outbound SSRF guard rejects `http://`, `127.0.0.1`, `10.x.x.x`, `172.16.x.x`, `192.168.x.x`, `169.254.169.254`, and `[::1]` with a typed SSRF error, while permitting allowlisted `https://` hosts.
- [x] Circuit breaker transitions cleanly across `CLOSED` → `OPEN` → `HALF_OPEN` → `CLOSED` under simulated failures and recovery probes, and thread-safety tests prove no race conditions under concurrent load.
- [x] `ModelGateway` enforces token budgets and timeouts, trips the circuit breaker on repeated model failures, and returns a safe fail-closed advisory decision without ever widening policy authority.
- [x] Versioned evaluation dataset (§117) runs in `pytest` and verifies 100% pass rate on canonical commerce, prompt-injection, and conflict scenarios.

### Transactional Outbox, Event Bus & Workers (R2)
- [x] Outbox events are persisted in both in-memory and SQLAlchemy (`infrastructure/database/`) repositories with tenant isolation (`tenant_id`).
- [x] Background worker (`apps/worker/`) dispatches pending outbox events to registered `EventBus` handlers idempotently (duplicate delivery is a no-op), marks events processed, and handles failed deliveries with retry/backoff metadata.
- [x] Worker reconciliation job transitions stuck `UNKNOWN` payments and expires stale `PENDING` approvals in automated unit/integration tests.

### Abuse Detection & Observability (R3)
- [x] Simulated credential stuffing, catalog scraping, and tool-loop abuse trigger `SecurityEvent` emission, append to the SHA-256 hash-chained audit ledger, and automatically transition the offending agent to `QUARANTINED` or `SUSPENDED`.
- [x] Telemetry spans and metrics (`trace_id`, `span_id`, policy decision counters, payment status counters, webhook failure counters) are recorded during API/orchestrator execution and verified via tests.

### Accessibility, SBOM & Repository Integrity (R4)
- [x] `apps/web/src/` components include explicit ARIA roles/attributes (`aria-*`, `role=`) and keyboard interaction support, verified by automated assertions in `node --test tests/web.test.mjs`.
- [x] `uv run python scripts/generate_sbom.py --help` and programmatic SBOM/provenance generation tests verify valid CycloneDX JSON and SHA-256 provenance digests from `uv.lock` and `package.json`.
- [x] All 4 CI gates (`uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, `uv run pytest`) and all auxiliary/adversarial suites pass with 0 errors, and `AGENTS.md`, `README.md`, `docs/engineering/known-limitations.md`, `docs/engineering/current-state.md`, and `docs/security/autonomous-audit-report.md` are updated to match the exact measured counts.

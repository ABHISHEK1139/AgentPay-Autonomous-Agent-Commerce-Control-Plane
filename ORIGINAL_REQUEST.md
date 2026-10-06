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

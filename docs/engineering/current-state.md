# Current State

Verified on this machine (Windows, PowerShell 5.1) with Python 3.12.10, uv 0.12.22, ruff 0.16.10,
mypy 2.4.0, pytest 9.1.1.

## What exists

| Component | State | Scope & Verification |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | locked | Dependencies locked, pure standard-library runtime |
| Monorepo layout (§9) | initialized | `apps/api`, `apps/worker`, `apps/web`, `packages/*`, `security/*` |
| Version Control | active | Git initialized, provenance tracked |
| Domain: `Money` | implemented | 32 unit & property-based tests (§122, §123) |
| Domain: `ActionRequest` | implemented | Canonical lifecycle abstraction (§6, §7) |
| Domain: `Identity & Tenancy` | implemented | Tenant, User, Agent, AgentPermissions (§14, §15, §16, §49) |
| Domain: `Commerce & Evidence` | implemented | Merchant, Product, Variant, Inventory, Conflict Detector (§30, §32) |
| Domain: `Decision & Approval` | implemented | JEV Decision models, ApprovalBinding, state machine (§18, §20) |
| Domain: `Payments & Orders` | implemented | PaymentAttempt, Order, Idempotency, Saga compensation (§39, §41, §42) |
| Domain: `Audit Ledger` | implemented | SHA-256 hash chaining, sensitive field redaction (§29, §45) |
| Domain: `Safety & Protection` | implemented | Loop tracker, tool call budget, financial safety budget (§64, §65) |
| Application Layer | implemented | `AgentAuthorizationService`, `ApprovalService`, `PurchaseOrchestrator` |
| Ports & Adapters | implemented | In-memory repositories & mock payment/decision adapters |
| HTTP API & Emergency Controls | implemented | Standard error envelopes (§13), OpenAPI 3.1, Kill switch (§21, §67) |
| Failure Engineering (§112) | verified | Chaos suite: price drift, inventory depletion, timeout reconciliation, saga |
| Red-Team Security (§95) | verified | Adversarial suite: prompt injection, privilege escalation, cross-tenant |
| Architecture Boundary Guard | verified | 7 tests enforce domain purity (§10) with zero external imports |

Verified command results:

```text
uv run ruff format --check .   52 files left unchanged
uv run ruff check .            All checks passed!
uv run mypy                    Success: no issues found in 52 source files
uv run pytest                  246 passed in 2.5s
```

All property-based tests completed their full budgets.

## Environment constraints

- `make`, `just`, and `psql` are **not installed**. The blueprint's §9 `Makefile`/`justfile` are
  therefore not provided; use the `uv run` commands instead.
- The **Docker daemon is not running**, so the declared Postgres/Redis services cannot start. All
  domain, application, API, chaos, and security suites are hermetic and run entirely in-memory.
- The project directory is named `platfrom` (one `a`). That is the intended path.
- Shell is PowerShell 5.1: no `&&`, no heredocs.

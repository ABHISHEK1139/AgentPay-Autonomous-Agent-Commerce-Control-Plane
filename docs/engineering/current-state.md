# Current State

Verified on this machine (Windows, PowerShell 5.1) with Python 3.12.10, Node v22.14.0, uv 0.12.22, ruff 0.16.10,
mypy 2.4.0, pytest 9.1.1.

## What exists

| Component | State | Scope & Verification |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | locked | Dependencies locked, pure standard-library runtime |
| Monorepo layout (§9) | initialized | `apps/api`, `apps/worker`, `apps/web`, `packages/*`, `adapters/*`, `security/*` |
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
| Protocol Adapters | implemented | ACP, AP2, UAP, x402 adapters mapping to `ActionRequest` (§107, §108) |
| Web Control Center | implemented | `apps/web/`: 10-step inspectable pipeline, approval UI (§66), kill switch UI (§67) |
| Ports & Adapters | implemented | In-memory repositories & mock payment/decision adapters |
| HTTP API & Emergency Controls | implemented | Standard error envelopes (§13), OpenAPI 3.1, Kill switch (§21, §67) |
| Failure Engineering (§112) | verified | Chaos suite: price drift, inventory depletion, timeout reconciliation, saga |
| Red-Team Security & 14 Vectors (§177) | verified | Exhaustive adversarial threat harness, crypto tamper suite, concurrency stress suite |
| Architecture Boundary Guard | verified | 7 tests enforce domain purity (§10) with zero external imports |

Verified command results:

```text
uv run ruff format --check .   67 files already formatted
uv run ruff check .            All checks passed!
uv run mypy                    Success: no issues found in 64 source files
uv run pytest                  383 passed
node --test tests/web.test.mjs 3 passed in 99ms
```

All property-based tests completed their full budgets.

## Environment constraints

- `make`, `just`, and `psql` are **not installed**. The blueprint's §9 `Makefile`/`justfile` are
  therefore not provided; use the `uv run` and `node --test` commands instead.
- The **Docker daemon is not running**, so the declared Postgres/Redis services cannot start. All
  domain, application, API, protocol adapter, chaos, and security suites are hermetic and run entirely in-memory.
- The project directory is named `platfrom` (one `a`). That is the intended path.
- Shell is PowerShell 5.1: no `&&`, no heredocs.

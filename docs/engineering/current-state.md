# Current State

Verified on this machine (Windows, PowerShell 5.1) with Python 3.12.10, Node v22.14.0, uv 0.12.22, ruff 0.16.10,
mypy 2.4.0, pytest 9.1.1.

## What exists

| Component | State | Scope & Verification |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | locked | Dependencies locked (including `sqlalchemy>=2.0.0`), zero forbidden imports in domain |
| Monorepo layout (§9) | verified | `apps/api`, `apps/web`, `apps/worker`, `packages/*`, `scripts/`, `security/`, `tests/`, `docs/*`. |
| Version Control | active | Git initialized, provenance tracked |
| Domain: `Money` & ISO 4217 `currencies` | implemented | 58 ISO 4217 currencies (0/2/3-decimal exponents), integer decimal parsing, rational `ExchangeRate` (§122, §123, TD-002 resolved) |
| Domain: `ActionRequest` | implemented | Canonical lifecycle abstraction (§6, §7) |
| Domain: `Identity & Tenancy` | implemented | Tenant, User, Agent, AgentPermissions, Credential, TokenClaims (§14, §15, §16, §49) |
| Domain: `Commerce & Evidence` | implemented | Merchant, Product, Variant, Inventory, Conflict Detector (§30, §32) |
| Domain: `Decision & Approval` | implemented | JEV Decision models with prompt/tool/policy metadata (§29, §118), ApprovalBinding, state machine (§18, §20) |
| Domain: `Payments & Orders` | implemented | PaymentAttempt, Order, Idempotency, Saga compensation (§39, §41, §42) |
| Domain: `Audit Ledger` | implemented | SHA-256 hash chaining, sensitive field redaction (§29, §45) |
| Domain: `Safety & Protection` | implemented | Loop tracker, tool call budget, financial safety budget, sliding window RateLimiter (§52, §62, §64, §65) |
| Domain: `Tool Registry` | implemented | Fixed tool registry, ToolSpec/ToolDefinition, capability gating, risk classification (§26, §27) |
| Application Layer | implemented | `AgentAuthorizationService`, `ApprovalService`, `PaymentReconciler` (with webhook state-machine guards), `PurchaseOrchestrator` |
| Protocol Adapters | implemented | ACP, AP2, UAP, x402 adapters mapping to canonical `ActionRequest` (§107, §108) |
| Developer SDK | implemented | Thin typed Python client (`from agentpay import AgentPay`, §109) |
| Web Control Center | implemented | `apps/web/`: 10-step inspectable pipeline, approval UI (§66), kill switch UI (§67) |
| Ports & Adapters | implemented | Thread-safe in-memory repositories & mock payment/decision adapters |
| Relational Persistence (§49) | implemented | SQLAlchemy 2.0 models and tenant-scoped repositories (`infrastructure/database/`), verified on in-memory SQLite (`test_database_persistence.py`) |
| HTTP API, Webhooks & Emergency Controls | implemented | Standard error envelopes with trace_id (§13, §47), OpenAPI 3.1, HMAC-SHA256 Payment Webhooks (`POST /api/v1/webhooks/payments`, §61), Kill switch (§21, §67), least-privilege `ApiTestClient` |
| Operational Runbooks | implemented | All 9 named runbooks in `docs/runbooks/` (§133) |
| Engineering Journal | implemented | `docs/engineering/experiments.md` documenting EXP-001 through EXP-006 (§87) |
| Security Assurance | implemented | OWASP ASVS v4.0.3 verification matrix, YAML index, exceptions in `security/asvs/` (§53) |
| Failure Engineering (§112) | verified | Chaos suite: price drift, inventory depletion, timeout reconciliation, saga |
| Red-Team Security & 14 Vectors (§177) | verified | Exhaustive adversarial threat harness, crypto tamper suite, concurrency stress suite |
| Architecture Boundary Guard | verified | 7 tests enforce domain purity (§10) with zero external imports |

Verified command results:

```text
uv run ruff format --check .                                    121 files already formatted
uv run ruff check .                                             All checks passed!
uv run mypy                                                     Success: no issues found in 109 source files
uv run pytest                                                   566 passed in 5.03s
node --test tests/web.test.mjs                                  3 passed in 344ms
uv run python tests/adversarial/stress_test_protocols.py        42/42 passed
uv run python tests/adversarial/stress_test_concurrency.py      4/4 passed
uv run python tests/adversarial/stress_test_client_privilege.py 5/5 passed
uv run python tests/adversarial/stress_test_money.py            6/6 passed
uv run python tests/adversarial/stress_test_webhooks.py         6/6 passed
uv run python scripts/verify_audit_chain.py --help              Verified working
```

All property-based tests completed their full budgets.

## Environment constraints

- `make`, `just`, and `psql` are **not installed**. The blueprint's §9 `Makefile`/`justfile` are
  therefore not provided; use the `uv run` and `node --test` commands instead.
- The **Docker daemon is not running**, so the declared Postgres/Redis services cannot start. All
  domain, application, API, protocol adapter, chaos, and security suites are hermetic and run entirely in-memory.
- The project directory is named `platfrom` (one `a`). That is the intended path.
- Shell is PowerShell 5.1: no `&&`, no heredocs.

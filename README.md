# AgentPay

An agent action-control and transaction platform: AI agents can perform real-world actions on
behalf of a human or organisation, but never with unrestricted authority.

**This repository implements the Complete Autonomous Control Plane (Sprints 0–12)**.
All core domain models, identity & tenancy boundaries, commerce catalog & evidence conflict detection,
decision intelligence & human approval gating, payments & saga compensation, immutable audit ledger,
HTTP API layer with OpenAPI & emergency kill switch, external protocol adapters (ACP, AP2, UAP, x402),
interactive Control Center web dashboard, failure engineering chaos tests, and the Section 177
adversarial threat harness covering all 14 mandatory attack vectors are fully implemented and verified.

## The blueprint is `AGENTS.md`

`AGENTS.md` in the repository root is the authoritative product, architecture, engineering and
security specification. It is binding, not aspirational background reading. Read the section that
governs your change (see its §0.5 index) before writing code.

## Status

| Area | State |
| --- | --- |
| Tooling (uv, ruff, mypy, pytest, node, CI) | working, fully locked |
| CLI & Server Runtime (`agentpay serve`, `agentpay worker`, `agentpay health`, etc.) | implemented, zero-dependency multi-threaded HTTP server (§8, §147) |
| Standalone Packages (`agentpay-sdk`, `agentpay-protocol-types`, `agentpay-schemas`, `agentpay-test-utils`) | implemented in `packages/` (§9, §109) |
| Domain model (`Money` + ISO 4217 `currencies`, `ActionRequest`, `Identity`, `Commerce`, `Evidence`, `Decision`, `Approval`, `Payments`, `Audit`, `Safety`, `Tools`, `OutboxEvent`, `SecurityEvent`) | implemented, unit + property tested |
| Application Layer (`AgentAuthorizationService`, `ApprovalService`, `PaymentReconciler`, `PurchaseOrchestrator`, `EventBus`, `AbuseDetector`) | implemented, hermetic orchestration |
| Resilience & Security (`CircuitBreaker`, `RetryPolicy`, outbound SSRF filter, `ModelGateway`, rate limiting, token budget) | implemented and verified (§23, §60, §102, §103) |
| Background Worker Runtime (`OutboxDispatcher`, `BackgroundWorker`, reconciliation & approval sweeps) | implemented and verified (§104, §105, §106) |
| Observability (Zero-dependency `Tracer`, `Span`, `Meter`, and `GET /api/v1/metrics`) | implemented and verified (§47) |
| Supply Chain (CycloneDX 1.5 JSON SBOM & SLSA 1.2 build provenance CLI) | implemented and verified (§57, §130) |
| Protocol Adapters (ACP, AP2, UAP, x402) | implemented, pure domain mapping (§107, §108) |
| Web Control Center (`apps/web/`) | implemented, 10-step inspectable pipeline, live API client integration (§14, §21, §66, §67), approval UI (§66), kill switch UI (§67), ARIA accessibility (§121) |
| Adapters (In-memory repos, SQLAlchemy 2.0 relational repos with Alembic migrations, Stripe & Razorpay payment adapters with HMAC webhooks, mock JEV decision engine) | implemented, multi-tenant isolated |
| HTTP API Layer (`/api/v1/...`, `/agent/...`, `POST /api/v1/webhooks/payments`, `GET /api/v1/metrics`, emergency pause/resume) | implemented, ASGI standard-library |
| Live Socket E2E Network Tests | implemented, real TCP socket tests verifying HTTP server & SDK (§109) |
| Failure Engineering & Chaos Testing (§112) | implemented (price drift, timeout, decline saga, oversell) |
| Red-Team Adversarial Corpus & 14 Vectors (§177) | implemented and verified (all 14 vectors defended) |
| Dependency-boundary enforcement (§10) | enforced by 7 boundary tests (0 forbidden imports) |

## Quickstart (Zero to Live in 5 Minutes)

See [`docs/onboarding/quickstart.md`](docs/onboarding/quickstart.md) for the complete walkthrough.

```powershell
# 1. Sync environment
uv sync

# 2. Launch live server with demo data on http://127.0.0.1:8000
uv run agentpay serve

# 3. Check health
uv run agentpay health
```

## Verified commands

Run from the repository root. These are the exact commands CI runs.

```powershell
uv sync                             # create/update .venv from uv.lock
uv run ruff format --check .        # format verification (175 files formatted)
uv run ruff check .                 # lint (clean)
uv run mypy                         # strict type check (167 source files clean)
uv run pytest                       # 728 unit, property, chaos, persistence & live socket tests pass
node --test tests/web.test.mjs      # 5 web control center suites pass (incl. live API client & §121 accessibility)
```

Gate order is `ruff format --check` → `ruff check` → `mypy` → `pytest` → `node --test tests/web.test.mjs`.

## Layout

```text
agentpay/
├── apps/
│   ├── api/src/agentpay/
│   │   ├── domain/          business meaning; imports no framework (blueprint §10)
│   │   ├── application/     use cases, orchestration, purchase flow, approval & reconciliation
│   │   ├── api/             HTTP translation, OpenAPI, webhooks, error envelopes, emergency controls
│   │   ├── infrastructure/  SQLAlchemy 2.0 relational models and tenant-scoped repositories
│   │   ├── adapters/        in-memory repositories, mock payment provider, decision engine
│   │   ├── adapters/protocols/ ACP, AP2, UAP, x402 agent commerce protocol adapters
│   │   ├── server.py        zero-dependency multi-threaded HTTP server runner
│   │   └── cli.py           CLI entry point (agentpay serve, worker, health, migrate)
│   ├── api/tests/unit/      unit, property-based, chaos, persistence, adversarial & live socket tests
│   ├── worker/              standalone background outbox worker runtime daemon
│   └── web/                 React/TypeScript Control Center (10-step visualizer, approval UI, kill switch)
├── packages/
│   ├── sdk/                 standalone agentpay-sdk Python client library
│   ├── protocol-types/      standalone external commerce protocol types
│   ├── schemas/             canonical ActionRequest & API error envelope JSON schemas
│   └── test-utils/          in-process test doubles and client test helpers
├── docs/                    architecture, ADRs, threat model, runbooks, audit reports, technical debt
├── scripts/                 SBOM & SLSA provenance generator, audit chain verifier
└── tests/
    ├── adversarial/         concurrency, protocol, privilege, money, SSRF & model gateway stress harnesses
    └── web.test.mjs         Node test runner for web control center
```

The domain layer's import restrictions are enforced by
`apps/api/tests/unit/test_architecture_boundaries.py`. That test also verifies itself, so a
silently-broken checker fails instead of passing vacuously.

## Local development

Postgres and Redis are declared in `docker-compose.yml` for later production deployments. The Docker daemon is
not currently running on this machine, so all test suites run hermetically in-memory or on SQLite.

## Documentation

| Document | Purpose |
| --- | --- |
| `docs/onboarding/quickstart.md` | 5-minute beginner-to-production quickstart guide |
| `docs/onboarding/developer.md` | developer onboarding & workflow guide |
| `docs/architecture/architecture.md` | layering, boundaries, extension points, protocol architecture |
| `docs/decisions/` | ADRs; architectural design records (ADR-0001, ADR-0002) |
| `docs/threat-model/threat-model.md` | threat model & attack surface analysis (§55) |
| `docs/security/standards-matrix.md` | designed-for / aligned-with / tested-against mapping |
| `docs/security/autonomous-audit-report.md` | authoritative Section 177 autonomous audit report & Section 176 release certification |
| `docs/operations/incident-runbook.md` | emergency kill switch protocols & incident playbooks (§21, §67) |
| `docs/operations/backup-restore.md` | PITR disaster recovery & cryptographic hash chain verification (§45) |
| `docs/engineering/` | current state, known limitations, technical debt register |
| `CHANGELOG.md` | release history and version tracking |

## Security posture

The platform has undergone exhaustive adversarial verification across all 14 mandatory attack vectors
in Section 177, documented in `docs/security/autonomous-audit-report.md`. See
`docs/security/standards-matrix.md` for standards mapping and `SECURITY.md` for vulnerability reporting.

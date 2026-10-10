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
| Domain model (`Money`, `ActionRequest`, `Identity`, `Commerce`, `Evidence`, `Decision`, `Approval`, `Payments`, `Audit`, `Safety`) | implemented, unit + property tested |
| Application Layer (`AgentAuthorizationService`, `ApprovalService`, `PurchaseOrchestrator`) | implemented, hermetic orchestration |
| Protocol Adapters (ACP, AP2, UAP, x402) | implemented, pure domain mapping (§107, §108) |
| Web Control Center (`apps/web/`) | implemented, 10-step inspectable pipeline, approval UI (§66), kill switch UI (§67) |
| Adapters (In-memory repos, mock PSP, mock JEV decision engine) | implemented, multi-tenant isolated |
| HTTP API Layer (`/api/v1/...`, `/agent/...`, emergency pause/resume) | implemented, ASGI standard-library |
| Failure Engineering & Chaos Testing (§112) | implemented (price drift, timeout, decline saga, oversell) |
| Red-Team Adversarial Corpus & 14 Vectors (§177) | implemented and verified (all 14 vectors defended) |
| Dependency-boundary enforcement (§10) | enforced by 7 boundary tests (0 forbidden imports) |

## Verified commands

Run from the repository root. These are the exact commands CI runs.

```powershell
uv sync                             # create/update .venv from uv.lock
uv run ruff format --check .        # format verification (81 files formatted)
uv run ruff check .                 # lint (clean)
uv run mypy                         # strict type check (78 source files clean)
uv run pytest                       # 491 unit, property, chaos & adversarial tests pass
node --test tests/web.test.mjs      # 3 web control center suites pass
```

Gate order is `ruff format --check` → `ruff check` → `mypy` → `pytest` → `node --test tests/web.test.mjs`.

There is no `make`, `just`, or raw `pip` entry point. `make` and `just` are not installed on this
machine, so the `Makefile`/`justfile` named in blueprint §9 are not provided; the commands above
are the interface. Do not invent commands that do not exist.

## Layout

```text
apps/
    api/src/agentpay/
        domain/          business meaning; imports no framework (blueprint §10)
        application/     use cases, orchestration, purchase flow, approval service
        api/             HTTP translation, OpenAPI, error envelopes, emergency controls
        adapters/        in-memory repositories, mock payment provider, decision engine
        adapters/protocols/ ACP, AP2, UAP, x402 agent commerce protocol adapters
    api/tests/unit/      unit, property-based, chaos, protocol, and adversarial security tests
    web/                 React/TypeScript Control Center (10-step visualizer, approval UI, kill switch)
docs/                    architecture, ADRs, threat model, runbooks, audit reports, technical debt
tests/
    adversarial/         concurrency and protocol stress testing harnesses
    web.test.mjs         Node test runner for web control center
```

The domain layer's import restrictions are enforced by
`apps/api/tests/unit/test_architecture_boundaries.py`. That test also verifies itself, so a
silently-broken checker fails instead of passing vacuously.

## Local development

Postgres and Redis are declared in `docker-compose.yml` for later production deployments. The Docker daemon is
not currently running on this machine, so all test suites run hermetically in-memory.

## Documentation

| Document | Purpose |
| --- | --- |
| `docs/architecture/architecture.md` | layering, boundaries, extension points, protocol architecture |
| `docs/decisions/` | ADRs; architectural design records (ADR-0001, ADR-0002) |
| `docs/threat-model/threat-model.md` | threat model & attack surface analysis (§55) |
| `docs/security/standards-matrix.md` | designed-for / aligned-with / tested-against mapping |
| `docs/security/autonomous-audit-report.md` | authoritative Section 177 autonomous audit report & Section 176 release certification |
| `docs/operations/incident-runbook.md` | emergency kill switch protocols & incident playbooks (§21, §67) |
| `docs/operations/backup-restore.md` | PITR disaster recovery & cryptographic hash chain verification (§45) |
| `docs/engineering/` | current state, known limitations, technical debt register |
| `docs/onboarding/developer.md` | setup through first change |

## Security posture

The platform has undergone exhaustive adversarial verification across all 14 mandatory attack vectors
in Section 177, documented in `docs/security/autonomous-audit-report.md`. See
`docs/security/standards-matrix.md` for standards mapping and `SECURITY.md` for vulnerability reporting.

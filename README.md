# AgentPay

An agent action-control and transaction platform: AI agents can perform real-world actions on
behalf of a human or organisation, but never with unrestricted authority.

**This repository implements the Core Autonomous Control Plane (Sprint 0–9)**.
All core domain models, identity & tenancy boundaries, commerce catalog & evidence conflict detection,
decision intelligence & human approval gating, payments & saga compensation, immutable audit ledger,
HTTP API layer with OpenAPI & emergency kill switch, failure engineering chaos tests, and red-team
adversarial test corpus are implemented and hermetically verified.

## The blueprint is `AGENTS.md`

`AGENTS.md` in the repository root is the authoritative product, architecture, engineering and
security specification. It is binding, not aspirational background reading. Read the section that
governs your change (see its §0.5 index) before writing code.

## Status

| Area | State |
| --- | --- |
| Tooling (uv, ruff, mypy, pytest, CI) | working, fully locked |
| Domain model (`Money`, `ActionRequest`, `Identity`, `Commerce`, `Evidence`, `Decision`, `Approval`, `Payments`, `Audit`, `Safety`) | implemented, unit + property tested |
| Application Layer (`AgentAuthorizationService`, `ApprovalService`, `PurchaseOrchestrator`) | implemented, hermetic orchestration |
| Adapters (In-memory repos, mock PSP, mock JEV decision engine) | implemented, multi-tenant isolated |
| HTTP API Layer (`/api/v1/...`, `/agent/...`, emergency pause/resume) | implemented, ASGI standard-library |
| Failure Engineering & Chaos Testing (§112) | implemented (price drift, timeout, decline saga, oversell) |
| Red-Team Adversarial Corpus (§95) | implemented (prompt injection, privilege escalation, cross-tenant) |
| Dependency-boundary enforcement (§10) | enforced by 7 boundary tests |

## Verified commands

Run from the repository root. These are the exact commands CI runs.

```powershell
uv sync                 # create/update .venv from uv.lock
uv run ruff format .    # format
uv run ruff check .     # lint
uv run mypy             # strict type check
uv run pytest           # 246 unit, property, chaos & adversarial tests
```

Gate order is `ruff format --check` → `ruff check` → `mypy` → `pytest`.

There is no `make`, `just`, or `npm` entry point. `make` and `just` are not installed on this
machine, so the `Makefile`/`justfile` named in blueprint §9 are not provided; the commands above
are the interface. Do not invent commands that do not exist.

## Layout

```text
apps/api/src/agentpay/
    domain/          business meaning; imports no framework (blueprint §10)
    application/     use cases and orchestration
    api/             HTTP translation, OpenAPI, error envelopes, emergency controls
    adapters/        in-memory repositories, mock payment provider, decision engine
apps/api/tests/unit/ unit, property-based, chaos, and adversarial security tests
docs/                architecture, ADRs, threat model, technical debt register, standards mapping
```

The domain layer's import restrictions are enforced by
`apps/api/tests/unit/test_architecture_boundaries.py`. That test also verifies itself, so a
silently-broken checker fails instead of passing vacuously.

## Local development

Postgres and Redis are declared in `docker-compose.yml` for later sprints. The Docker daemon is
not currently running on this machine, so nothing in the default test suite depends on it.

## Documentation

| Document | Purpose |
| --- | --- |
| `docs/architecture/architecture.md` | layering, boundaries, extension points |
| `docs/decisions/` | ADRs; anything structural gets one before it changes |
| `docs/threat-model/threat-model.md` | initial threat model (§55) |
| `docs/security/standards-matrix.md` | designed-for / aligned-with / tested-against mapping |
| `docs/engineering/` | current state, known limitations, technical debt register |
| `docs/onboarding/developer.md` | setup through first change |

## Security posture

Nothing here has been certified, penetration tested, or audited by a third party. See
`docs/security/standards-matrix.md` for the honest mapping and `SECURITY.md` for reporting.

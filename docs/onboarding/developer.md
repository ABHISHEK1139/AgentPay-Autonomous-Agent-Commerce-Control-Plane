# Developer Onboarding

## 1. Prerequisites

Python 3.12 and `uv`. Verified working on Windows / PowerShell 5.1 with Python 3.12.10 and uv
0.12.22. Not required to run the test suite: `make`, `just`, `psql`, or a running Docker daemon.

## 2. Setup

```powershell
uv sync
```

This creates `.venv` from the committed `uv.lock`. Use `uv run <command>` for every command below;
it resolves the environment automatically, so no activation step is needed.

## 3. Run the quality gates

Order matters — formatting first, because lint and type errors are easier to read on formatted code.

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

All four must be clean before a change is considered done (blueprint §77). CI runs exactly these
commands, so a local pass and a CI pass mean the same thing.

## 4. Focused runs

```powershell
uv run pytest apps/api/tests/unit/test_policy.py
uv run pytest -k daily
uv run pytest -x -q
uv run pytest --hypothesis-show-statistics
```

The last flag proves property-based tests really ran their example budget rather than passing
vacuously.

## 5. Where things live

- Business meaning: `apps/api/src/agentpay/domain/`. Imports no framework — enforced by test.
- Architecture and boundaries: `docs/architecture/architecture.md`.
- Structural decisions: `docs/decisions/`. Write an ADR before changing a boundary, per blueprint §82.
- Threat model: `docs/threat-model/threat-model.md`. Update it when architecture changes.
- What exists, what does not, and what is broken: `docs/engineering/`.

## 6. Make your first change

A safe first exercise: add a currency-comparison scenario to
`apps/api/tests/unit/test_money.py`, or a new policy reason to `SpendingPolicyEngine` in
`apps/api/src/agentpay/domain/policy.py` — the latter is a good demonstration of the shape a
policy change should take: add a typed `PolicyReason`, keep the function pure, add a test for the
boundary and for monotonicity.

Do not add a dependency without checking whether the standard library suffices (blueprint §147).

## 7. Before you commit

- The four gates pass.
- No test was deleted or weakened to make something pass (blueprint §78). If a test is wrong, prove
  why, document it, fix it, and add the correct one.
- Anything structural has an ADR.
- New or changed behaviour is reflected in docs.
- No secrets, and no real customer data (blueprint §150).

## 8. Environment quirks that will waste your time

- The directory is named `platfrom` (one `a`). That is correct. Do not rename it.
- PowerShell 5.1: no `&&`, no heredocs. Use `; if ($?) { ... }`.
- The repository is not yet a git repository — there is no history to consult.
- `ruff format` is configured to skip `*.md`, so it will not rewrite the Python snippets inside
  `AGENTS.md`. Keep it that way: `AGENTS.md` is the blueprint.

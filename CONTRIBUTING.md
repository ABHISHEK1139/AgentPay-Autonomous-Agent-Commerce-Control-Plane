# Contributing

## The blueprint comes first

`AGENTS.md` is the authoritative specification. Before changing anything, find the section that
governs your change (its §0.5 is an index) and read it. Where your instinct and the blueprint
disagree, the blueprint wins — if you believe it is wrong, raise it with a human rather than
editing it away. Automated tooling must never rewrite it.

## Setup

```powershell
uv sync
```

## Gates — all four must pass

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

CI runs exactly these commands.

## Definition of done

A change is done when it works, is typed, is tested, is documented, and — if it moves a boundary —
has an ADR and an updated threat model. A change that alters money handling, authorization,
tenant scope, or payment semantics also needs a security review (blueprint §77).

## What will be rejected

- Deleted or weakened tests to get a green build.
- A new abstraction for something that does not exist yet (blueprint §171).
- Microservices as a reflex (see `docs/decisions/ADR-0001-modular-monolith-first.md`).
- Model output influencing permissions, limits, or policy.
- Claims of compliance, certification, or testing that have no evidence behind them.
- A commit message like "fix" or "final-final-working-version".

## Commit style

Small, single-purpose commits with a conventional prefix: `feat:`, `test:`, `docs:`, `security:`,
`refactor:`. Record AI authorship provenance where the work was agent-generated (blueprint §175).

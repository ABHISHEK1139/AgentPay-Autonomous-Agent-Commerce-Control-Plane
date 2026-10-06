# Known Limitations

Things a future engineer will otherwise discover the hard way.

## Product

- Nothing works end to end. There is no way to create an order, move money, or see a decision
  through a user interface. The vertical slice of blueprint §74 does not exist yet.
- No demo mode (§162), so the deterministic ALLOW / REVIEW / DENY scenarios of §112 cannot yet be
  demonstrated. The policy engine does implement the ALLOW / REVIEW / DENY distinctions those
  scenarios describe, at unit level.

## Architecture

- The blueprint's §9 monorepo layout is a **target**. Only `apps/api` exists. `apps/web`,
  `apps/worker`, `packages/`, `adapters/`, `infrastructure/`, and `security/` are not created
  because an empty directory is not a deliverable.
- `agentpay/domain/__init__.py` and the other layer packages are empty placeholders; the layering
  they will hold is described in `docs/architecture/architecture.md`, not yet demonstrated by code.

## Security

- No authentication, authorization, or tenant isolation is implemented, so none is tested. The
  blueprint's Sprint 1 work (§75) has not started.
- No automated security scanning runs in CI yet — no SAST, dependency scanning, secret scanning, or
  container scanning. `docs/security/standards-matrix.md` is candid about this.
- The threat model covers only what exists; TM-07 through TM-16 are placeholders for components not
  yet built.
- `Money` does not model a currency's minor-unit exponent, so JPY-style zero-decimal currencies are
  not fully handled (TD-002).

## Testing

- Coverage is unit-level plus property-based. There are no contract, integration, end-to-end,
  adversarial, performance, or chaos suites, because there are no adapters or services to test
  against.
- No test currently exercises concurrency, because nothing is stateful yet.

## Tooling

- The dependency-boundary test enforces imports for the `domain` layer only. The other layers are
  not yet implemented and are not yet governed.
- Line length is 100 and `E501` is disabled in the ruff configuration; overlong lines are not an
  error.

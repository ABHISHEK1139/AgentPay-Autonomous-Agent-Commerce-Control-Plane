# Security Policy

## Current state

AgentPay has not been security assessed, penetration tested, or certified against any standard. It
is at Sprint 0: a domain model, tooling, and tests. There is no deployed service, no authentication,
and no payment integration yet. See `docs/security/standards-matrix.md` for the honest mapping.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Do not open a public issue for
an unfixed vulnerability. Include the affected component, reproduction steps, and impact.

## Handling rules for contributors and agents

- Never commit credentials, API keys, payment secrets, or real customer data. Use
  `.env.example` to document a variable; use a secret manager for anything real (blueprint §58).
- Never disable authentication, weaken a test, or suppress a security finding to make something
  pass (blueprint §135).
- AI output is untrusted input. It can never set permissions, spending limits, policy, or
  credentials (blueprint §2, §18).
- Payment data minimisation is a hard rule: no card numbers, no CVV, no unnecessary payment
  credentials, ever (blueprint §40).
- When money, identity, price, inventory, or policy is uncertain, fail closed — do not execute
  (blueprint §2.4).

## Scope note

The `AGENTS.md` blueprint is authoritative and must not be modified by automated tooling. CI fails
if a tool rewrites it.

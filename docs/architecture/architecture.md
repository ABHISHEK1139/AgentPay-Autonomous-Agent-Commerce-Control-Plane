# AgentPay — Architecture

Status: Sprint 0. Describes what exists now and the boundaries that must not move.

## Layering

```text
api  ──▶  application  ──▶  domain  ◀──  ports
                                          ▲
                              infrastructure / adapters
```

Dependency direction is one-way. `agentpay.domain` imports nothing from any other layer and
nothing from a framework. This is not a convention, it is a test:
`apps/api/tests/unit/test_architecture_boundaries.py` walks the AST of every domain module and
fails on any import of FastAPI, SQLAlchemy, Redis, HTTP clients, payment/AI SDKs, or an outer
AgentPay layer.

The current import restriction list lives in that test's `FORBIDDEN_IN_DOMAIN`. Adding a
dependency to the domain layer means changing that list deliberately, in a reviewed diff.

## What exists

```text
domain/errors.py           DomainError hierarchy, each with a stable machine-readable code
domain/money.py            Money (integer minor units + currency), total_money
domain/action_request.py   ActionRequest, ResourceRef, Intent, AuthorizationMode, ActionStatus
domain/policy.py           PolicyDecision, PolicyReason, PolicyEvaluationResult, SpendingLimits,
                           SpendingPolicyEngine
```

Everything is a frozen, slotted dataclass. There is no ORM, no framework, no I/O, and no clock in
the domain package, which is why it is directly unit-testable.

### Money

Integer minor units plus an explicit currency. Floats, `Decimal`, strings and `bool` are rejected
in the constructor. Currency codes are validated for shape (three A–Z letters, normalised to upper
case) rather than against a hard-coded table, so no currency is privileged in the domain core.
Subtraction refuses to produce a negative amount rather than wrapping.

### ActionRequest

The canonical abstraction that every future domain normalises into. Validates that identity fields
are present, that `requested_at` is timezone-aware, and that a requested amount does not exceed the
stated intent budget. `authorization_mode` defaults to `SUPERVISED`: authority is requested
explicitly and is never inferred from an absent field.

### Policy

`PolicyEvaluationResult` returns one of `ALLOW`, `DENY`, `REVIEW`, `NEEDS_INFORMATION` with a
policy version and typed reasons — never a bare boolean. `SpendingPolicyEngine` is a pure function
of its inputs: no clock, no randomness, no I/O, so the same inputs always produce the same result.
When several rules fire, the most restrictive decision wins and all reasons are retained, because a
DENY that cannot be explained is unauditable.

## Deliberately absent

No HTTP layer, no persistence, no payment provider, no AI integration. Each is a separate sprint.
Adding them means adding ports and adapters, not editing domain logic.

## Extension points and their seams

| When you need | Add | Do not |
| --- | --- | --- |
| a new domain (procurement, travel) | a domain module + adapter behind a port | generalise `ActionRequest` into a base class hierarchy |
| a payment provider | an adapter satisfying the payment port | import an SDK into the domain |
| a model vendor | a model-gateway adapter | let a model set limits or policy |
| a second policy rule | a rule returning typed `PolicyReason`s | let a rule return a boolean |

## Known architectural debt

Recorded in `docs/engineering/technical-debt.md` (TD-001 …). The main one: the blueprint's §9
monorepo layout is a target, and only the parts that exist today have been created.

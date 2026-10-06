# ADR-0002: Represent money as integer minor units

## Status

Accepted

## Context

Blueprint §123 requires that money never uses binary floating point. Amounts arrive from HTTP
payloads, from payment providers, and from merchant catalogues, in several shapes: a JSON number
(`1499.50`), a JSON string (`"1499.50"`), or a provider's own integer minor-unit field.

## Problem

Choosing the in-memory and storage representation for money, and deciding where conversion from
decimal input happens.

## Options

1. `float` — rejected: `0.1 + 0.2 != 0.3`, and the error grows with magnitude.
2. `decimal.Decimal` — exact for decimal input, but carries a precision context, and mixing it with
   `int` or a provider integer reintroduces rounding decisions at every call site.
3. `int` minor units plus an explicit currency code, converted at the boundary.
4. Store a string everywhere — exact, but unusable for comparison, aggregation, and policy limits.

## Decision

Option 3. `Money(minor_units: int, currency: str)` is a frozen value object. The constructor
rejects anything that is not a genuine `int` — including `float`, `Decimal`, `str`, and `bool`.
Currency is validated for shape (three A–Z letters, normalised to upper case) and is never assumed
to be a particular country's currency: blueprint §122 forbids hard-coding `INR` into the domain
core, and no currency registry is baked in.

Decimal strings from the wire are parsed once, explicitly, at the API boundary. Nothing in the
domain accepts a decimal string.

## Why

The invariant that matters is that a total is exactly the sum of its parts, with no rounding
decision anywhere in the middle. Integer minor units make that structural: addition is integer
addition. It also makes currency-mixing a loud error rather than a silent numeric result, and it
makes the representation identical in the domain, in the database, and in the API, so no conversion
layer can drift between them.

A fixed currency registry was deliberately not embedded: it would bloat the domain and create a
second thing to keep current besides the ISO standard.

## Consequences

- `Money.subtract` raises rather than producing a negative amount.
- Any new provider adapter must convert its representation at the adapter edge.
- Minor-unit exponent varies by currency (JPY has no decimal subunit). `Money` records the amount
  and its currency but does not yet carry an exponent — tracked as TD-002, and it must be resolved
  before multi-currency commerce is implemented, not before the MVP.
- Presentation formatting (symbols, grouping) belongs at the presentation boundary.

## Alternatives rejected

- `Decimal`: correct but slower to reason about, and its precision context makes correctness depend
  on call-site discipline — exactly the class of bug the blueprint is trying to eliminate.
- `float` plus rounding at each step: rounding is a policy decision that would have to be made in
  many places and audited in all of them.

## Security impact

Prevents a whole class of rounding-based limit bypass: a limit check performed on a float could be
made to pass or fail by representation error rather than by the amount actually requested. It also
guarantees audit records of amounts are exact and independently reproducible.

## Revisit when

Multi-currency commerce begins (resolve TD-002 first), or a use case needs fractional minor units
beyond a currency's own exponent.

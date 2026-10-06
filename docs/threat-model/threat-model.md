# Initial Threat Model

Status: **initial / draft**. Covers Sprint 0 only. It must be revised whenever architecture
changes (blueprint §56). No entry here has been penetration tested.

## Scope of this revision

Only the domain layer exists. There is no network surface, no persistence, no identity provider, no
payment integration, and no model integration yet. Threats that require those components are
recorded as **planned**, with the control that must exist before the component ships — not as
mitigations that are already in place.

## Actors and trust boundaries

| Actor | Trust | Notes |
| --- | --- | --- |
| Human principal | partially trusted | authenticated identity, but intent is input to be validated |
| AI agent | untrusted | never trusted; authority is granted by policy, never by the model |
| LLM / decision engine | untrusted output | may be adversarial, buggy, or manipulated by its input |
| Merchant | untrusted data source | product text and prices are claims, not facts |
| Payment provider | partially trusted | responses must be verified, not assumed |
| Third-party API | untrusted | outbound requests are a risk, not a convenience |

## Assets

| Asset | Why it matters |
| --- | --- |
| Spending limits and policy | bypassing one is direct financial loss |
| Agent credentials | impersonation yields the agent's entire authority |
| Payment credentials | direct loss; must never be stored (§40) |
| Tenant data | cross-tenant exposure is a P1 (§97) |
| Audit ledger | without it, no incident can be reconstructed |
| Approval grants | a reused or widened approval is a financial bypass |

## Threats relevant to what exists now

### TM-01 — Float sneaking into a monetary path

- **Asset:** spending limits, audit accuracy
- **Attacker:** any caller, including a careless future contributor
- **Entry point:** a `float` constructed directly, or a decimal string parsed in the domain
- **Impact:** a limit check decided by representation error; unauditable amounts
- **Mitigation:** `Money` rejects non-`int` minor units; the domain accepts no decimal strings
- **Test:** `apps/api/tests/unit/test_money.py::test_non_integer_minor_units_are_rejected`,
  `::test_decimal_minor_units_never_drift`

### TM-02 — Inverted or missing limit check

- **Asset:** spending limits
- **Attacker:** implementation defect rather than an external attacker
- **Entry point:** a comparison written against the wrong operand — this occurred in the first
  implementation of the `ActionRequest` budget check and was caught by tests
- **Impact:** requests above a stated budget accepted, or valid requests rejected
- **Mitigation:** property-based monotonicity and ceiling properties
- **Test:** `test_increasing_amount_can_never_reduce_required_strictness`,
  `test_amount_never_exceeds_configured_ceiling_when_allowed`

### TM-03 — Authority inferred from a missing field

- **Asset:** agent authority
- **Entry point:** an absent `authorization_mode` defaulting to the most permissive value
- **Impact:** an agent silently acquiring autonomous authority
- **Mitigation:** fail-closed default (`SUPERVISED`)
- **Test:** `test_omitting_authorization_mode_yields_supervised_not_autonomous`

### TM-04 — Domain layer acquiring authority over infrastructure

- **Asset:** authorization boundary, tenant isolation
- **Entry point:** business logic gaining the ability to query the database or call a provider
  directly, bypassing policy and audit
- **Impact:** policy bypass paths that are invisible to review
- **Mitigation:** AST-enforced import boundary, with the checker itself tested
- **Test:** `apps/api/tests/unit/test_architecture_boundaries.py`

### TM-05 — Unauditable denial

- **Asset:** audit ledger, operator trust
- **Entry point:** a `DENY` or `REVIEW` with no reason, or a boolean instead of a structured result
- **Impact:** an operator cannot reconstruct why an action was blocked, and abuse is invisible
- **Mitigation:** structured `PolicyEvaluationResult`; every denial carries typed reasons
- **Test:** `test_any_denial_always_carries_an_explanatory_reason`

### TM-06 — Naive timestamps in audit records

- **Asset:** audit trail, approval expiry
- **Entry point:** a naive `datetime` reaching persistence
- **Impact:** audit entries that cannot be ordered across tenants or timezones; expiry comparisons
  that silently use local time
- **Mitigation:** timezone-aware datetimes required at construction
- **Test:** `test_naive_datetime_is_rejected`

## Planned threats — not yet mitigable

These become real when the corresponding component is built. Each needs its control in place
*before* the component ships.

| ID | Threat | Required control before shipping |
| --- | --- | --- |
| TM-07 | Cross-tenant data access | `tenant_id` mandatory on every repository method; negative tests per resource (§50) |
| TM-08 | Prompt injection via merchant text | all external text treated as data; fixed permissioned tool registry (§25, §26) |
| TM-09 | Model output setting limits or policy | model cannot reach the policy engine's inputs; policy is deterministic (§18) |
| TM-10 | Approval replay or widening | approval binds action, amount, merchant, resource, currency, version; single use (§20) |
| TM-11 | Duplicate payment | idempotency keys on all financial operations (§42) |
| TM-12 | Payment timeout double-charge | query provider on timeout; never blind retry (§43) |
| TM-13 | SSRF via merchant or tool URLs | scheme and host allowlists, private-range blocking, redirect validation (§60) |
| TM-14 | Payment status forged from client redirect | server-side provider verification; payment state separate from order state (§41) |
| TM-15 | Audit tampering | tamper-evident chaining, then immutable storage (§45, §46) |
| TM-16 | Credential compromise | secret manager, rotation, short-lived agent tokens (§14, §58) |

## Method

STRIDE for system threats, LINDDUN where privacy modelling applies, plus AI-specific categories for
prompt injection, tool abuse, and model manipulation. The full pass for each component happens when
that component is designed, not at the end.

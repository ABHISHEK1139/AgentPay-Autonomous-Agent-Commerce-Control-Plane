# ADR-0007: Approval Binding Extended to 9 Fields with Agent Identity

## Status

Accepted. Supersedes the field list of ADR-0004 (8 fields) and strengthens
blueprint §20 (8 listed fields) by adding `agent_id` as a ninth bound field.
The §20 amendment is recorded here rather than by editing the blueprint's
numbered section, per §0.5.

## Context

ADR-0004 established an explicit 8-field deterministic binding in
`ApprovalBinding`: `(action_id, amount, merchant_id, resource_id, currency,
action_version, principal_id, tenant_id)`.

A residual intra-tenant substitution vector remained: an approval granted for
agent A could be consumed by agent B acting for the same principal in the same
tenant. The blueprint's authorization question (§16) is "Can THIS principal,
using THIS agent, perform THIS action..." — the binding answered for principal
and tenant but not for the agent identity actually executing the transaction.

Commit `20a9953` shipped the fix (`ApprovalBinding.agent_id`, migration
`0006_add_approval_agent_id`, domain/service/repository enforcement) without
the ADR §82 requires before changing the authorization model or the persistent
data model. This ADR records that change retroactively and makes the 9-field
list the single source of truth.

## Problem

How should approval bindings also prevent agent substitution, without
fabricating authority for pre-migration rows or weakening the atomic single-use
guarantee?

## Options

1. **Keep 8 fields; treat agent identity as out of scope.**
   *Rejected*: leaves a concrete substitution vector. Two agents sharing a
   principal (e.g. a fleet of shopping agents) could replay each other's
   approvals. §16 explicitly binds the agent into the authorization question.

2. **Bind `agent_id` as a 9th field (selected).** Add `agent_id: str` to
   `ApprovalBinding` with non-empty validation, enforce it in
   `ApprovalService.verify_binding`, the in-memory and SQLAlchemy repositories,
   the API binding views, and the orchestrator's pre/post-consume verification.
   Persist via migration `0006_add_approval_agent_id` (`approvals.agent_id`,
   `ix_approvals_agent_id`, `ix_approvals_tenant_agent`).

3. **Bind a hash of the agent's credential instead of its identity.**
   *Rejected*: introduces credential-material handling for no security gain
   over binding the stable `agent_id`; complicates rotation and audit
   readability.

## Decision

Adopt explicit 9-field deterministic binding in `ApprovalBinding`:

1. `action_id: str`
2. `amount: Money` (minor units + currency)
3. `merchant_id: str`
4. `resource_id: str`
5. `currency: str`
6. `action_version: int`
7. `principal_id: str`
8. `tenant_id: str`
9. `agent_id: str` — **new in this ADR**

All 9 fields must be verified at grant, execution, and consumption time. Any
mismatch raises `ApprovalBindingMismatchError` and fails closed. Consumption
remains atomic and single-use (`atomic_consume`), enforced via database
conditional UPDATE / mutex-guarded in-memory stores (§20, §177 Vector 10).

The approval lifecycle and policy evaluation order are unchanged: revalidation
of lifecycle, category, price, inventory, evidence, and merchant state runs
*before* the atomic consume, so transient drift fails without burning the
single-use approval. This ordering fix landed with the same hardening batch
and does not alter the field list.

## Why

The executing agent is part of the authority being delegated. A human approves
"agent A may spend ₹8,000 at merchant M for resource R" — not "any agent for
this principal." Binding `agent_id` closes the last intra-tenant substitution
gap identified by the Section 177 red-team review (Vector 10 replay plus agent
impersonation, §143) while keeping the binding human-readable for the §66
approval screen.

## Consequences

- **Security**: cross-tenant replay, principal substitution, and agent
  substitution are all rejected fail-closed at execution time.
- **Persistence**: `approvals` gains `agent_id` via migration `0006`
  (`NOT NULL`, `server_default=""`, two indexes). See the operational note
  below — the empty default is deliberate and requires an operator action,
  not a silent backfill.
- **API**: `GET /api/v1/approvals` and `GET /api/v1/approvals/{id}` expose
  `agent_id` alongside `principal_id` and `tenant_id` for the Control Center.
- **Domain purity**: unchanged; `ApprovalBinding` still imports only stdlib
  and domain primitives (`Money`).
- **Docs**: all "8-field" references outside the blueprint's numbered §20 are
  updated to 9-field; ADR-0004 remains accepted as history, superseded on the
  field list by this ADR.

## Alternatives Rejected

- See Options above. In particular, binding the agent's *credential* was
  rejected in favor of its stable identity.

## Security Impact

Enforces the authorization question of §16 end-to-end (principal + agent +
action + resource + context), mitigates Section 143 Vectors 3 (agent
impersonation) and 10 (approval replay), and preserves the ASVS V11.1.2
single-use binding property recorded in `security/asvs/` (whose field list
is updated alongside this ADR). No new cryptography is introduced.

## Operational Note (migration 0006)

Pre-0006 approval rows receive `agent_id=''` from the migration. The domain
layer rejects empty `agent_id`, so legacy rows cannot be loaded or executed —
fail-closed by construction. Approvals are short-lived (pending windows);
operators must expire and re-issue any approval created before the migration.
There is deliberately **no backfilled agent identity**: inventing one would
fabricate authority. The same `""`-default pattern exists for `principal_id`
(migration `0003`) and carries the same expire-and-reissue instruction.

## Revisit When

- A multi-principal delegation model (§17 delegation) requires binding a
  delegator chain rather than a single `principal_id` / `agent_id` pair.
- Agent identity becomes federated across tenants (cross-tenant delegation),
  which would require a credential-issuer field in the binding.

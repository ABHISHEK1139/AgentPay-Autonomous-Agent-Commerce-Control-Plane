# ADR-0004: Explicit 8-Field Approval Binding with Principal and Tenant Identity

## Status

Accepted

## Context

AgentPay governs high-impact autonomous actions through human approval workflows (§20, §66).
An approval represents a delegation of human authority to an AI agent to execute a specific transaction.

In the original specifications, a contract discrepancy existed:
- The blueprint (`AGENTS.md` §20) originally listed 6 binding fields: action, amount, merchant, resource, currency, action version.
- The repository `README.md` listed 7 fields: action, amount, currency, merchant, resource, principal, action version (omitting tenant).
- The initial domain implementation (`ApprovalBinding`) bound 7 fields: action_id, amount, merchant_id, resource_id, currency, action_version, tenant_id (omitting principal).

Remediation finding AP-006 identified this discrepancy as a critical authorization flaw: omitting `principal_id` permitted intra-tenant approval substitution (Agent B acting for User Bob could consume an approval created by User Alice), while omitting `tenant_id` would compromise multi-tenant isolation.

## Problem

How should approval bindings enforce human authorization and organizational isolation without contract ambiguity or replay vulnerability?

## Options Considered

1. **7-field binding with principal only**: Drop `tenant_id` and rely on `principal_id` prefixes or user lookup.
   *Rejected*: Violates core multi-tenant boundary (§49, §50). Cross-tenant queries could conflict or leak if principal IDs collided across tenants.
2. **7-field binding with tenant only**: Retain existing implementation.
   *Rejected*: Violates human authorization identity (§2, §20). Fails to prevent intra-tenant privilege escalation or approval theft across users.
3. **Explicit 8-field binding with both principal and tenant**:
   Bind `(action_id, amount, merchant_id, resource_id, currency, action_version, principal_id, tenant_id)`.
   *Selected*.

## Decision

Adopt explicit 8-field deterministic binding in `ApprovalBinding`:
1. `action_id: str`: The specific `ActionRequest` identifier.
2. `amount: Money`: Exact integer minor units and currency.
3. `merchant_id: str`: Authorized merchant/seller identifier.
4. `resource_id: str`: Targeted product or resource identifier.
5. `currency: str`: ISO 4217 3-letter currency code.
6. `action_version: int`: Version of the action request, preventing post-approval payload mutation.
7. `principal_id: str`: The authenticated human principal (buyer/delegator) whose authority is granted.
8. `tenant_id: str`: The organizational tenant boundary.

All 8 fields must be verified at grant, execution, and consumption time. Any mismatch immediately raises `ApprovalBindingMismatchError` and fails closed.

Approval consumption must remain atomic and single-use (`atomic_consume`), enforced via database row-level locking / atomic SQL UPDATE in relational storage and thread locks in memory.

## Consequences

- **Security**: Completely prevents both cross-tenant approval replay and intra-tenant principal substitution.
- **Persistence**: Table `approvals` is migrated via Alembic migration `0003_add_approval_binding_principal.py` to add `principal_id`.
- **API**: Endpoints `GET /api/v1/approvals` and `GET /api/v1/approvals/{approval_id}` expose both `principal_id` and `tenant_id` to the Control Center UI (§66).
- **Domain Purity**: Domain models remain 100% pure Python standard library and domain primitives (`Money`).

## Security Impact

Enforces OWASP ASVS v4.0 Section V4 (Access Control Verification) and mitigates Section 177 Vector 10 (Replay attacks on human approval bindings).

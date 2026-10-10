# AgentPay Product Specification & Architecture Overview (§1–§7)

> **Document Status**: Production Product Blueprint  
> **Governing Specifications**: `AGENTS.md` (§1 Product Definition, §2 Philosophy, §3 Users, §4 Boundaries, §5 UX, §6 ActionRequest, §7 Domain Model)  
> **Last Updated**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Executive Product Definition (§1)

**AgentPay is an autonomous agent action-control and transaction platform.**

It allows AI agents to perform real-world actions on behalf of humans or enterprise organizations while deterministically enforcing:
- **Identity & Authentication**: Cryptographically bound credentials, principal-to-tenant verification.
- **Authorization & Permissions**: Least privilege capability gating, fixed tool registries.
- **Financial Limits**: Minor-unit budgeting, daily/monthly spend caps, multi-tier approval thresholds.
- **Evidence Verification**: Cross-referencing quoted prices against merchant feeds; conflict detection.
- **Human Approval**: Exact 7-field cryptographic binding for human-in-the-loop signoff.
- **Safe Execution & Recovery**: Idempotent payments, saga compensation rollbacks, read-only timeout reconciliation.
- **Auditability**: SHA-256 hash-chained immutable audit ledger.

The first commercial domain is **commerce**. The core abstraction (`ActionRequest`) is domain-neutral and built to accommodate future domains (procurement, travel, SaaS licensing, machine-to-machine commerce).

---

## 2. Core Philosophy & Invariants (§2)

1. **AI sits below the authorization boundary (§2.1, §18)**:  
   AI models (LLMs, JEV) can interpret, recommend, plan, and request actions. They can **never** independently grant permissions, alter spending caps, bypass policy rules, or manage payment credentials.
2. **Fail closed for financial operations (§2.4)**:  
   When uncertainty affects money, identity, inventory, price, or policy: `UNKNOWN + HIGH IMPACT = DO NOT EXECUTE`. The platform blocks, asks for human review, or reconciles.
3. **Money is integer minor units (§122)**:  
   No floating point mathematics. Never hard-code currency or locales into core domain rules.
4. **Evidence before action (§2.3)**:  
   Every material transaction must possess an evidence trail proving source prices, inventory availability, and policy conformance.

---

## 3. Product Users & Personas (§3, §5)

### 3.1 Consumer Persona
Simple, intuitive safeguards:
- Maximum spend per purchase (e.g. ₹2,000 / $50).
- Daily cumulative budget (e.g. ₹5,000 / $100).
- Mandatory human approval for high-risk or out-of-policy purchases.

### 3.2 Enterprise Persona
Comprehensive organizational control plane:
- Organization → Policies → Departments → Budgets → Agents → Approvals → Audit Ledger.
- Fine-grained role-based access control (Admin, Operator, User, Auditor).
- Merchant whitelisting and procurement catalog controls.

---

## 4. The Core Abstraction: `ActionRequest` (§6)

Every action in the system is expressed through the canonical `ActionRequest` object:

```json
{
  "action_id": "act_88019a2b",
  "tenant_id": "tenant_enterprise_01",
  "principal_id": "usr_ops_lead",
  "agent_id": "ag_procurement_bot",
  "domain": "commerce",
  "action": "purchase",
  "resource": {
    "type": "product_variant",
    "id": "var_laptop_01"
  },
  "amount": {
    "minor_units": 149900,
    "currency": "INR"
  },
  "intent": {
    "purpose": "developer workstation equipment",
    "budget_max_minor_units": 200000
  },
  "authorization": {
    "mode": "AUTONOMOUS"
  }
}
```

### 10-Step Lifecycle Pipeline
```
USER INTENT 
  ↓
AGENT IDENTITY & AUTHENTICATION
  ↓
PRODUCT DISCOVERY & SELECTION
  ↓
EVIDENCE & CONFLICT DETECTION
  ↓
RECOMMENDATION GENERATION
  ↓
JEV / ADVISORY DECISION
  ↓
DETERMINISTIC POLICY EVALUATION
  ↓
HUMAN APPROVAL (IF REQUIRED)
  ↓
IDEMPOTENT PAYMENT CAPTURE
  ↓
ORDER FULFILLMENT & SAGA COMPENSATION
```

---

## 5. Domain Model Entities (§7)

- **Identity**: `Tenant`, `User`, `Agent`, `Credential`, `UserRole`, `AgentStatus`.
- **Authorization**: `Permission`, `Policy`, `ApprovalPolicy`, `ApprovalTier`.
- **Evidence**: `Evidence`, `EvidenceSource`, `EvidenceConflict`, `SponsorshipDisclosure`.
- **Commerce**: `Merchant`, `Product`, `ProductVariant`, `Inventory`, `Money`.
- **Payments**: `PaymentIntent`, `PaymentAttempt`, `PaymentStatus`, `PaymentReconciler`.
- **Governance**: `AuditEvent`, `AuditHasher`, `SensitiveFieldRedactor`, `RateLimiter`.

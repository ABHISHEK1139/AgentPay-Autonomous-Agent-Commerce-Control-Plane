# AgentPay — Architecture

> **Status**: Remediation Complete — Full Autonomous Control Plane Verified (Engineering Preview)  
> **Governing Specifications**: `AGENTS.md` (§8, §10, §18, §21, §45, §67, §107, §108, §176)  

---

## 1. Layering & Architectural Flow

The architecture follows a strict **modular monolith** with clean hexagonal boundaries.

```text
┌─────────────────────────────────────────────────────────────┐
│                       UI / CLIENTS                          │
│     Web Control Center (apps/web)  •  External Agents       │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / Protocols
┌──────────────────────────────▼──────────────────────────────┐
│                    API & PROTOCOL ADAPTERS                  │
│ AgentPayApiApp (Zero-Dep HTTP) • ACP • AP2 • UAP • x402     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Commands / DTOs
┌──────────────────────────────▼──────────────────────────────┐
│                      APPLICATION LAYER                      │
│   PurchaseOrchestrator  •  ApprovalService  •  AuthService  │
└──────────────────────────────┬──────────────────────────────┘
                               │ Domain Operations
┌──────────────────────────────▼──────────────────────────────┐
│                         DOMAIN CORE                         │
│  ActionRequest • Money • Policy • Identity • Evidence       │
│  Commerce • Payments • Audit Hash Ledger • Safety Loops     │
└──────────────────────────────▲──────────────────────────────┘
                               │ Implements Ports
┌──────────────────────────────┴──────────────────────────────┐
│                    PORTS & INFRASTRUCTURE                   │
│ In-Memory & SQLAlchemy Repos • Sandbox & Mock Payment Gateways│
└─────────────────────────────────────────────────────────────┘
```

### 1.1 Dependency Direction
Dependency direction is strictly inward:
- **`agentpay.domain`** contains business rules and invariants. It imports zero frameworks, zero ORMs, zero HTTP clients, and zero payment/AI SDKs.
- **Enforcement (§10)**: `apps/api/tests/unit/test_architecture_boundaries.py` walks the AST of every domain module and fails if any forbidden import is introduced (`FORBIDDEN_IN_DOMAIN`). 7 tests enforce this boundary.
- **Application Layer**: Coordinates domain workflows (`execute_purchase`, `execute_approved_purchase`, `grant_approval`).
- **Protocol Adapters**: Translate external agent protocols (ACP, AP2, UAP, x402) into the canonical `ActionRequest` without leaking external protocol constructs into the domain core.
- **Infrastructure Layer**: Implements domain repository interfaces and external service ports.

---

## 2. Core Domain Components

| Module | Responsibility & Key Domain Entities | Governing Invariants |
|---|---|---|
| `domain/money.py` | `Money`, `total_money`. Integer minor units + ISO currency. | Never binary float or bool (§122). Subtraction refuses negative results. |
| `domain/action_request.py` | `ActionRequest`, `ResourceRef`, `Intent`, `AuthorizationMode`, `ActionStatus`. | Canonical representation of all agent actions (§6). Timezone-aware UTC. |
| `domain/identity.py` | `Tenant`, `User`, `Agent`, `AgentPermissions`, `AgentStatus`. | Agents cannot alter permissions. Non-active agents fail closed (§14, §15). |
| `domain/policy.py` | `SpendingLimits`, `SpendingPolicyEngine`, `PolicyEvaluationResult`, `PolicyDecision`. | Pure deterministic function. No clock, no network, most restrictive rule wins (§18). |
| `domain/commerce.py` | `Merchant`, `Product`, `ProductVariant`, `Inventory`, `MerchantStatus`. | Stock reservation state machine. Suspended merchants fail closed (§30, §37). |
| `domain/evidence.py` | `EvidenceConflictDetector`, `EvidenceRecord`, `ConflictType`. | Pre-flight price and stock conflict detection. Fails closed before payment (§32, §33). |
| `domain/decision.py` | `Decision`, `DecisionRecommendation`, `RiskLevel`. | Advisory AI (JEV / LLM). Strictly subordinate to deterministic policy engine (§2.2, §134). |
| `domain/approval.py` | `Approval`, `ApprovalBinding`, `ApprovalStatus`, `ApprovalTier`, `ApprovalPolicy`. | Exact 8-field binding to action, amount, merchant, resource, currency, version, tenant, principal. Single-use consumption (§20). |
| `domain/payments.py` | `PaymentAttempt`, `PaymentStatus`, `Order`, `OrderStatus`, `Refund`, `IdempotencyRecord`. | Strict idempotency keys. On timeout, query provider; never blindly retry (§41, §43). |
| `application/services/reconciliation_service.py` | `PaymentReconciler`. | Read-only provider query for UNKNOWN attempts; persists outcome; never creates, captures, or refunds (§43). |
| `domain/audit.py` | `AuditEvent`, `AuditHasher`, `SensitiveFieldRedactor`, `AuditEventType`. | SHA-256 hash chaining over canonical JSON. Full metadata preimage validation (§29, §45). |
| `domain/safety.py` | `AgentLoopBudget`, `AgentLoopTracker`, `FinancialSafetyBudget`. | Halts recursive agent tool loops (max 5 calls, max depth 3) (§64, §65). |

---

## 3. Protocol Adapters (§107, §108)

Located in `apps/api/src/agentpay/adapters/protocols/`:
- **`AcpAdapter` (Agent Commerce Protocol)**: Translates ACP checkout intents and cart structures into `ActionRequest`.
- **`Ap2Adapter` (Agent Payment Protocol)**: Handles AP2 mandates, verifications, and execution bindings. Enforces mandate expiry.
- **`UapAdapter` (Universal Agent Protocol)**: Translates UAP JSON-RPC 2.0 messages into domain actions. Rejects unsupported capabilities and malformed envelopes.
- **`X402Adapter` (HTTP 402 Micropayments)**: Manages L402/x402 challenge-response headers, macaroons/tokens, and micropayment verification.
- **Emergency Halt**: All 4 protocol adapters intercept requests when the kill switch is engaged, returning HTTP 503 `EMERGENCY_PAUSE_ACTIVE` (§21, §67).

---

## 4. Control Center Frontend (`apps/web/`)

Built as an interactive, inspectable operator dashboard:
- **10-Step Pipeline Visualizer**: Renders the complete agent decision lifecycle:
  `USER INTENT → AGENT IDENTITY → PRODUCT DISCOVERY → EVIDENCE → RECOMMENDATION → JEV → POLICY → APPROVAL → PAYMENT → ORDER`
- **Human Approval Review Screen (§66)**: Displays all 11 decision context fields (who, what, why, amount, merchant, evidence provenance, risk level, confidence score, etc.).
- **Emergency Kill Switch Controls (§21, §67)**: One-click cluster pause and resume with operator attribution.

---

## 5. Deliberately Deferred & Operational Seams

The following components are designed, implemented, and verified with in-memory or SQLite adapters, ready for production deployment:
- **Relational Persistence & Migrations**: SQLAlchemy 2.0 models and tenant-partitioned repositories are implemented (`infrastructure/database/`), verified against in-memory SQLite, and managed via Alembic migrations (`0001` through `0004`). Production deployment targets PostgreSQL with WAL archiving (ADR-0001).
- **Distributed Cache & State**: Emergency pause and webhook deduplication persist to SQLite/PostgreSQL; in-process threading primitives provide fast-path caching.
- **Payment Sandboxes (§151)**: Payment processing operates against real sandbox (`RazorpaySandboxProvider`) or mock adapters with the complete state machine and saga compensation lifecycle. Live-money processing is strictly disabled.

---

## 6. Extension Points & Seams

| Scenario | Prescribed Approach | Prohibited Action |
|---|---|---|
| Adding new agent domain (travel, procurement) | Add domain entities & adapters behind ports | Do NOT generalize `ActionRequest` into a complex inheritance hierarchy. |
| Connecting live payment gateway (Razorpay) | Implement payment adapter satisfying the payment port | Do NOT import payment SDKs into the domain layer. |
| Connecting LLM / model provider | Add model gateway adapter satisfying decision port | Do NOT allow model outputs to override deterministic spending policy. |
| Custom protocol support | Add new adapter in `adapters/protocols/` | Do NOT let protocol-specific DTOs leak into domain entities. |

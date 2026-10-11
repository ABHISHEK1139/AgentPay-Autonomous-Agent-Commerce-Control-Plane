# AgentPay

<p align="center">
  <strong>Autonomous Agent Commerce Control Plane</strong><br>
  <em>Deterministic authorization, spending boundaries, human approvals, and financial rails for autonomous AI agents.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/mypy-strict%20(167%20files)-green.svg" alt="Mypy Strict">
  <img src="https://img.shields.io/badge/ruff-formatted%20(175%20files)-green.svg" alt="Ruff">
  <img src="https://img.shields.io/badge/pytest-748%20passed-brightgreen.svg" alt="Pytest 748 Passed">
  <img src="https://img.shields.io/badge/node%20test-5%20passed-brightgreen.svg" alt="Node Test">
  <img src="https://img.shields.io/badge/architecture-domain%20pure%20(AST%20enforced)-blue.svg" alt="Domain Purity">
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License Apache 2.0">
</p>

---

## What is AgentPay?

As AI agents gain the capability to browse the web, compare tools, negotiate prices, and execute tasks, they are increasingly expected to perform commercial transactions: purchasing software, booking flights, provisioning cloud resources, or acquiring physical goods.

**However, AI models must never possess direct authority over credit cards, payment gateways, or spending limits.** A single prompt injection, agent loop, or hallucination can exhaust budgets or commit organizations to unauthorized liabilities.

**AgentPay is the control plane between AI agents and real financial execution rails.** It treats AI agents as untrusted actors that propose intent through a canonical `ActionRequest`, while enforcing deterministic authorization, spending policies, evidence verification, human-in-the-loop approvals, atomic payments, and immutable auditability.

```text
┌────────────────┐      ┌─────────────────────────┐      ┌────────────────────┐
│ Autonomous AI  │ ───► │   AgentPay Control Plane │ ───► │ Payment Rails &    │
│ Agent (Untrusted)│      │  (Deterministic AuthZ)  │      │ Merchants (Stripe/ │
└────────────────┘      └─────────────────────────┘      │ Razorpay/SaaS)     │
                                     │                   └────────────────────┘
                                     ▼
                        ┌─────────────────────────┐
                        │ Human-in-the-Loop       │
                        │ Cryptographic Approvals │
                        └─────────────────────────┘
```

---

## Non-Negotiable Invariants

AgentPay is engineered according to core guarantees that can never be bypassed:

1. **AI is Below the Authorization Boundary (§2, §18)**: LLMs and decision models can interpret, recommend, and plan, but they can **never** set permissions, modify spending policies, or override human decisions. Deterministic policy engines retain absolute authority.
2. **Fail-Closed on Uncertainty (§2.4)**: If price, identity, inventory, policy, or currency is uncertain or inconsistent: **halt and fail closed**.
3. **Exact 7-Field Bound Approvals (§20)**: Human approvals are cryptographically bound to exact parameters: amount, currency, merchant ID, action ID, resource, principal, and action version. Approvals are strictly single-use; replays are rejected fail-closed.
4. **Integer Minor Units & Currencies (§122)**: Zero floating-point math. All financial arithmetic uses integer minor units (e.g. `149900` for ₹1,499.00 or $1,499.00) validated against ISO 4217 currency exponent tables.
5. **Tamper-Evident SHA-256 Audit Ledger (§45)**: Every evaluation, decision, policy check, and payment attempt is permanently recorded in a cryptographically chained SHA-256 audit ledger with automatic PII/PCI redaction.
6. **Multi-Tenant Isolation (§49–§51)**: Every repository method and service explicitly requires a `tenant_id`. Cross-tenant access is rejected fail-closed.

---

## 10-Step Transaction Lifecycle

```text
  1. ActionRequest Received   ──► Canonical intent schema submitted by agent
  2. Identity & Lifecycle     ──► Authenticate agent, verify active token & expiry
  3. Scope & Permissions      ──► Verify capability scopes and category whitelist
  4. Tool Registry Guard      ──► Strict parameter validation, sanitize untrusted data
  5. Advisory Intelligence    ──► JEV / LLM evaluates risk score & merchant consistency
  6. Deterministic Policy     ──► Authoritative check against hard spending limits
  7. Human Approval Gating    ──► High-impact or threshold actions yield PENDING_APPROVAL
  8. Inventory & Price Check  ──► Verify physical stock and detect catalog price drift
  9. Payment & Saga Capture   ──► Idempotent payment capture; rollback compensation on failure
 10. Chained Audit Ledger     ──► Immutable SHA-256 ledger entry with PII/PCI redaction
```

---

## Quickstart (5 Minutes)

### Prerequisites

- **Python 3.12+**
- **[uv](https://astral.sh/uv)** (fast Python package manager)
- **Node.js 18+** (for the Control Center web UI)

### 1. Installation

```powershell
# Clone the repository
git clone https://github.com/your-org/agentpay.git
cd agentpay

# Synchronize hermetic Python environment from uv.lock
uv sync
```

### 2. Launch the Control Plane API Server

Start the zero-dependency, multi-threaded HTTP server with demo data on `http://127.0.0.1:8000`:

```powershell
uv run agentpay serve
```

Verify server health:

```powershell
uv run agentpay health
```

### 3. Launch the Web Control Center UI

In a separate terminal, launch the interactive React/TypeScript Control Center:

```powershell
cd apps/web
npm install
npm run dev
```

Visit `http://localhost:5173` to explore:
- **Interactive 10-Step Pipeline Visualizer**: Inspect each stage of autonomous execution in real time.
- **8 Pre-Configured Scenarios (§112)**: Autonomous low-cost purchase, manager approval gating, daily limit breach, merchant price drift, payment decline saga compensation, oversell rollback, evidence conflict denial, and emergency kill-switch pause.
- **Human Approval Screen (§66)**: Review and approve actions with full decision context.
- **Emergency Kill Switch (§21, §67)**: Instant global or tenant-scoped operational pause.

---

## Python SDK Quickstart

The standalone `agentpay-sdk` (`packages/sdk/`) provides a typed, zero-dependency client:

```python
from agentpay_sdk import AgentPayClient

# Initialize client
client = AgentPayClient(base_url="http://127.0.0.1:8000")

# 1. Search merchant catalog
products = client.search_catalog("developer laptop")
print(f"Found {len(products)} products")

# 2. Submit autonomous purchase action
result = client.submit_purchase(
    agent_id="agent_01",
    product_id="prod_01",
    variant_id="var_01",
    amount_minor=129900,  # ₹1,299.00
    currency="INR",
    purpose="developer hardware procurement",
)

if result.status == "COMPLETED":
    print(f"Purchase successful! Order ID: {result.order_id}")
elif result.status == "PENDING_APPROVAL":
    print(f"Gated by policy: Requires human approval (ID: {result.approval_id})")
```

---

## Protocol Interoperability

AgentPay includes pure domain adapters for major external agent commerce standards:

| Protocol | Version | Status | Description |
|---|---|---|---|
| **ACP** (Agent Commerce Protocol) | v1.0 | Aligned & Tested | Standardized action requests, catalog discovery, and checkout lifecycle. |
| **AP2** (Agent Payment Protocol) | v2.0 | Aligned & Tested | Tokenized merchant authorization and payment attempt orchestration. |
| **UAP** (Universal Agent Protocol) | v1.1 | Aligned & Tested | Agent capability discovery, task delegation, and evidence exchange. |
| **x402** | v1.0 | Aligned & Tested | HTTP 402 Payment Required integration for agent-to-agent microtransactions. |

---

## Quality Gates & Verification

Every commit to AgentPay is verified against strict local and CI quality gates:

```powershell
# 1. Code style & formatting (175 files clean)
uv run ruff format --check .

# 2. Strict linting rules
uv run ruff check .

# 3. Strict static type checking (167 files clean, 0 errors)
uv run mypy

# 4. Pytest test suite (748 passed, 0 failed, 0 skipped)
uv run pytest

# 5. Web Control Center test suite (5 passed, ARIA §121 & live client)
node --test tests/web.test.mjs
```

### Auxiliary Security & Stress Probes

```powershell
uv run python tests/adversarial/stress_test_money.py                    # ISO 4217 & currency arithmetic
uv run python tests/adversarial/stress_test_webhooks.py                 # HMAC-SHA256 & replay prevention
uv run python tests/adversarial/stress_test_protocols.py                # Protocol adapter edge cases
uv run python tests/adversarial/stress_test_ssrf_resilience.py          # Outbound SSRF & circuit breakers
uv run python tests/adversarial/stress_test_model_gateway_resilience.py # Model gateway fallback
uv run python scripts/verify_audit_chain.py --help                      # SHA-256 audit chain verifier
uv run python scripts/generate_sbom.py --verify                         # CycloneDX 1.5 SBOM & SLSA provenance
```

---

## Primary API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/actions` | Submit an `ActionRequest` for autonomous evaluation and execution. |
| `GET` | `/api/v1/actions/{id}` | Retrieve execution result, decision summary, and order status. |
| `POST` | `/api/v1/approvals/{id}/grant` | Grant human approval for a pending action. |
| `POST` | `/api/v1/approvals/{id}/execute` | Execute an action that has received human approval. |
| `POST` | `/api/v1/webhooks/payments` | Ingest and reconcile signed Stripe/Razorpay payment webhooks. |
| `POST` | `/api/v1/system/pause` | Emergency kill switch: pause autonomous actions globally or by tenant. |
| `POST` | `/api/v1/system/resume` | Resume operations after resolving an incident. |
| `GET` | `/api/v1/metrics` | Real-time operational counters, latencies, and transaction metrics. |
| `GET` | `/api/v1/security/sbom` | Export CycloneDX 1.5 Software Bill of Materials. |
| `GET` | `/api/v1/health` | Service health and component status check. |

---

## Repository Layout

```text
agentpay/
├── apps/
│   ├── api/src/agentpay/
│   │   ├── domain/          # Pure Python core models; 0 framework imports (§10 enforced)
│   │   ├── application/     # Use cases, purchase orchestrator, reconciliation, outbox
│   │   ├── api/             # HTTP routing, error envelopes, webhooks, emergency controls
│   │   ├── infrastructure/  # SQLAlchemy 2.0 models, migrations, tenant-scoped repos
│   │   ├── adapters/        # In-memory repositories, Stripe & Razorpay adapters, mock JEV
│   │   ├── adapters/protocols/ # ACP, AP2, UAP, x402 protocol adapters
│   │   ├── server.py        # Pure standard-library multi-threaded HTTP server
│   │   └── cli.py           # CLI runner (agentpay serve, worker, health, migrate)
│   ├── api/tests/unit/      # 748 unit, property, chaos, adversarial & socket tests
│   ├── worker/              # Distributed outbox worker runtime with lease management
│   └── web/                 # React/TypeScript Control Center (pipeline visualizer & UI)
├── packages/
│   ├── sdk/                 # Standalone agentpay-sdk Python client library
│   ├── protocol-types/      # Standalone protocol dataclasses and schemas
│   ├── schemas/             # Canonical JSON schemas for ActionRequest & error envelopes
│   └── test-utils/          # In-process test doubles and fixtures
├── docs/                    # Architecture, ADRs, threat model, runbooks, security audits
├── scripts/                 # SBOM generator, audit chain verifier CLI
└── tests/
    ├── adversarial/         # Dedicated empirical stress test probes
    └── web.test.mjs         # Node web test runner
```

---

## Documentation Roadmap

- **[Quickstart Guide](docs/onboarding/quickstart.md)** — Zero to running in 5 minutes.
- **[Developer Guide](docs/onboarding/developer.md)** — Workflow, test flags, and contribution tips.
- **[Architecture Specification](docs/architecture/architecture.md)** — Domain boundaries, ports, and sagas.
- **[Architecture Decision Records](docs/decisions/)** — ADR-0001 (Modular Monolith), ADR-0002.
- **[Threat Model & Attack Surface](docs/threat-model/threat-model.md)** — STRIDE threat analysis.
- **[Autonomous Audit Report](docs/security/autonomous-audit-report.md)** — Section 177 threat harness verification.
- **[Open Bug Register](BUGS.md)** — Catalog of discovered and verified bug resolutions.
- **[Incident Runbook](docs/operations/incident-runbook.md)** — Emergency pause and operational procedures.

---

## License

AgentPay is open-source software licensed under the **[Apache License, Version 2.0](LICENSE)**.

# AgentPay Quickstart Guide

> **Get from zero to running autonomous agent commerce in 5 minutes.**

AgentPay is an agent action-control and transaction platform. It allows AI agents to perform real-world actions on behalf of humans and organizations while strictly enforcing identity, authorization, spending policies, evidence verification, human approvals, and cryptographic auditability.

---

## 1. Prerequisites

You only need Python 3.12+ and `uv` (the fast Python package manager).

### Installing `uv` (if not already installed)

- **macOS / Linux:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows (PowerShell):**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

---

## 2. Setup Your Environment

Clone the repository and synchronize the environment:

```powershell
# Install all dependencies into a hermetic virtual environment (.venv)
uv sync
```

That's it! `uv sync` reads the locked dependencies from `uv.lock`. You never need to manually activate virtual environments; just prefix commands with `uv run`.

---

## 3. Launch the Live HTTP Server

AgentPay comes with a zero-dependency, production-ready multi-threaded HTTP server and CLI:

```powershell
# Launch the API server on http://127.0.0.1:8000 with pre-seeded demo data
uv run agentpay serve
```

### CLI Command Options

```text
Usage: agentpay serve [OPTIONS]

Options:
  --host TEXT         Bind host [default: 127.0.0.1]
  --port INTEGER      Bind port [default: 8000]
  --db TEXT           Database path or connection string [default: in-memory]
  --webhook-secret    HMAC-SHA256 payment webhook secret
  --with-web          Serve compiled web UI from apps/web/dist if present
  --no-demo-data      Do not seed baseline demo entities (starts empty)
```

In another terminal, check server health:

```powershell
uv run agentpay health --url http://127.0.0.1:8000
```
Output:
```text
[OK] AgentPay is healthy: status=healthy, version=0.1.0, timestamp=2026-10-11T...
```

You can also inspect live Prometheus metrics and OpenAPI specifications:
- **Metrics**: `http://127.0.0.1:8000/api/v1/metrics`
- **OpenAPI Schema**: `http://127.0.0.1:8000/api/v1/openapi.json`

---

## 4. Run the Background Outbox Worker

AgentPay uses a transactional outbox pattern to guarantee at-least-once asynchronous event delivery, reconciliation sweeps, and approval expiration:

```powershell
# Run the background worker in standalone daemon mode
uv run agentpay worker --interval 2.0
```

---

## 5. Your First Autonomous Agent Transaction

The pre-seeded demo environment comes with:
- **Tenant ID**: `tenant_01`
- **Agent ID**: `agent_01` (Autonomous purchase limit: ₹2,000 / 200,000 minor units)
- **Product ID**: `prod_01` (Developer Workstation SSD, ₹1,499 / 149,900 minor units)
- **Variant ID**: `var_01`

### Option A: Using the Python Developer SDK

```python
from agentpay import AgentPay

# Initialize client pointing to your live server
client = AgentPay(
    base_url="http://127.0.0.1:8000",
    tenant_id="tenant_01",
    api_key="test_bearer_agent_token",
)

# 1. Discover catalog products
products = client.list_products()
print(f"Discovered {len(products)} products in catalog:")
for p in products:
    print(f" - {p['id']}: {p['title']}")

# 2. Execute an autonomous purchase under the spending limit
response = client.purchase(
    agent_id="agent_01",
    product_id="prod_01",
    variant_id="var_01",
    amount_minor_units=149900,
    currency="INR",
    intent="Autonomous hardware requisition",
)

print(f"Purchase Status: {response['status']}")
print(f"Order ID: {response['order_id']}")
print(f"Action Request ID: {response['action_request_id']}")
```

### Option B: Using cURL / HTTP Directly

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/purchases `
  -H "Authorization: Bearer test_bearer_agent_token" `
  -H "Content-Type: application/json" `
  -d '{
    "tenant_id": "tenant_01",
    "agent_id": "agent_01",
    "product_id": "prod_01",
    "variant_id": "var_01",
    "amount_minor_units": 149900,
    "currency": "INR",
    "intent": "Hardware upgrade for CI agent"
  }'
```

---

## 6. Human Approval Flow (Exceeding Spending Limits)

When an agent attempts a transaction that exceeds its autonomous policy limit (or triggers risk policies), AgentPay **fails closed** and routes the action for human review:

1. **Trigger Human Review**:
   Request a purchase of ₹4,999 (499,900 minor units) — higher than the ₹2,000 autonomous limit:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/purchases `
     -H "Authorization: Bearer test_bearer_agent_token" `
     -H "Content-Type: application/json" `
     -d '{
       "tenant_id": "tenant_01",
       "agent_id": "agent_01",
       "product_id": "prod_01",
       "variant_id": "var_01",
       "amount_minor_units": 499900,
       "currency": "INR",
       "intent": "High-value workstation purchase"
     }'
   ```
   **Response**:
   ```json
   {
     "status": "PENDING_APPROVAL",
     "action_request_id": "act_...",
     "approval_id": "appr_...",
     "policy_evaluation": {
       "decision": "REQUIRES_APPROVAL",
       "reasons": ["EXCEEDS_SINGLE_TRANSACTION_LIMIT"]
     }
   }
   ```

2. **Approve via Control Plane (Admin Persona)**:
   A designated human approver can review the cryptographically bound context and approve:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/approvals/<APPROVAL_ID>/approve `
     -H "Authorization: Bearer test_bearer_admin_token" `
     -H "Content-Type: application/json" `
     -d '{
       "tenant_id": "tenant_01",
       "reason": "Approved high-priority hardware requisition"
     }'
   ```

---

## 7. Emergency Operations: The Kill Switch

AgentPay includes an in-process and persistent Emergency Kill Switch. When triggered, all mutating operations across the platform **immediately reject with HTTP 503 (`EMERGENCY_PAUSE_ACTIVE`)**, while read-only auditing and diagnostics remain accessible.

```powershell
# Activate Emergency Pause
curl -X POST http://127.0.0.1:8000/api/v1/emergency/pause `
  -H "Authorization: Bearer test_bearer_admin_token" `
  -H "Content-Type: application/json" `
  -d '{"reason": "Investigating upstream anomaly"}'

# Test a purchase during pause (fails immediately with 503)
# HTTP/1.1 503 Service Unavailable
# {"code": "EMERGENCY_PAUSE_ACTIVE", "message": "Emergency pause active: Investigating upstream anomaly"}

# Resume Normal Operations
curl -X POST http://127.0.0.1:8000/api/v1/emergency/resume `
  -H "Authorization: Bearer test_bearer_admin_token" `
  -H "Content-Type: application/json" `
  -d '{"reason": "Investigation complete, all green"}'
```

---

## 8. Verifying Quality Gates & Test Suites

AgentPay maintains a zero-tolerance policy for test failures, lint errors, or type regressions. To run the full verification battery:

```powershell
uv run ruff format --check .       # Code style
uv run ruff check .                # Lint rules
uv run mypy                        # Strict static type checking (0 errors across 167 files)
uv run pytest                      # 736 unit, property, chaos, persistence & live socket tests
node --test tests/web.test.mjs     # 5 Control Center web tests (ARIA §121 & live client)
```

### Auxiliary Verification Tools

```powershell
# Verify CycloneDX 1.5 Software Bill of Materials & SLSA 1.2 build provenance
uv run python scripts/generate_sbom.py --verify

# Audit Ledger cryptographic SHA-256 chain verification tool
uv run python scripts/verify_audit_chain.py --help

# Section 177 Adversarial stress harnesses
uv run python tests/adversarial/stress_test_protocols.py
uv run python tests/adversarial/stress_test_webhooks.py
uv run python tests/adversarial/stress_test_concurrency.py
```

---

## 9. Next Steps

- Explore the architectural boundaries in [`docs/architecture/architecture.md`](../architecture/architecture.md).
- Review the security model and standards matrix in [`docs/security/standards-matrix.md`](../security/standards-matrix.md).
- Consult the operational runbooks in [`docs/runbooks/`](../runbooks/).

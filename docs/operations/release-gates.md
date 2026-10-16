# AgentPay — Release Verification Gates & Operational Checklist

> **Document Status**: Production Operational Standard  
> **Governing Specifications**: `AgentPay_Implementation_Remediation_Report.md` (AP-013, §4 Deliverable 4), `AGENTS.md` (§77, §98, §176)  
> **Last Updated**: 2026-10-16 local verification refresh

---

## 1. Release Philosophy (§77)

No artifact or release candidate may be tagged, packaged, or deployed until **all automated verification gates are 100% green**.
- Zero unexpected skips. Explicitly opt-in live-provider tests may be skipped when
  credentials or external infrastructure are intentionally unavailable.
- Zero xfail workarounds for genuine failures.
- Zero weakened assertions or lowered type checking strictness.
- Zero unauthorized modifications to `AGENTS.md` or domain boundaries.

---

## 2. Mandatory Verification Battery

The following suite must pass locally and in GitHub Actions CI (`.github/workflows/ci.yml`) on every pull request to `main`:

```powershell
# 1. Dependency Synchronization
uv sync --frozen

# 2. Code Style & Formatting (203 files clean)
uv run ruff format --check .

# 3. Static Analysis & Linting (clean, 0 warnings)
uv run ruff check .

# 4. Strict Type Checking (186 files clean, 0 errors)
uv run mypy

# 5. Full Pytest Suite (835 passed, 0 failed, 3 opt-in credential tests skipped)
uv run pytest

# 6. Web UI & Accessibility Suite (§121 ARIA & Keyboard Contracts)
node --test tests/web.test.mjs

# 7. Database Migration & Schema Integrity
uv run pytest apps/api/tests/unit/test_database_migrations.py

# 8. All 17 Dedicated Adversarial Stress Suites
uv run python tests/adversarial/stress_test_client_privilege.py
uv run python tests/adversarial/stress_test_concurrency.py
uv run python tests/adversarial/stress_test_m1_tenant_isolation.py
uv run python tests/adversarial/stress_test_m2_durable_webhooks.py
uv run python tests/adversarial/stress_test_m2_saga_concurrency.py
uv run python tests/adversarial/stress_test_m3_sandbox_config.py
uv run python tests/adversarial/stress_test_m4_approval_binding.py
uv run python tests/adversarial/stress_test_m4_challenger_edge_cases.py
uv run python tests/adversarial/stress_test_m6_shared_operational_controls.py
uv run python tests/adversarial/stress_test_m7_auth_deployment_profile.py
uv run python tests/adversarial/stress_test_m8_sandbox_e2e_reconciliation.py
uv run python tests/adversarial/stress_test_model_gateway_resilience.py
uv run python tests/adversarial/stress_test_money.py
uv run python tests/adversarial/stress_test_protocols.py
uv run python tests/adversarial/stress_test_ssrf_resilience.py
uv run python tests/adversarial/stress_test_webhook_crypto.py
uv run python tests/adversarial/stress_test_webhooks.py

# 9. Cryptographic Audit Chain CLI
uv run python scripts/verify_audit_chain.py --help

# 10. CycloneDX 1.5 SBOM & SLSA 1.2 Build Provenance Attestation
uv run python scripts/generate_sbom.py --verify

# 11. Blueprint Immutability Guard
git diff --name-only HEAD -- AGENTS.md
```

---

## 3. Database Migration & Deployment Lifecycle

### 3.1 Migration Execution Strategy
1. **Pre-Deployment Check**:
   - Inspect pending migrations:
     ```powershell
     uv run alembic current
     ```
2. **Execute Schema Migration**:
   - Apply migrations as an isolated deployment step prior to web service startup:
     ```powershell
     uv run alembic upgrade head
     ```
   - All migrations (`0001_initial_schema`, `0002_add_provider_webhook_events`, `0003_add_approval_binding_principal`, `0004_add_emergency_pause_records`) must execute cleanly.
3. **Application Startup**:
   - Web application validates schema version during health check. If pending migrations exist, startup fails closed.

### 3.2 Rollback Procedure
If a deployment incident occurs:
1. Trigger Emergency Kill Switch to halt in-flight agent transactions:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/emergency/pause -H "Authorization: Bearer <ADMIN_TOKEN>"
   ```
2. Roll back application binary or container image to previous release tag.
3. Downgrade database schema if migration rollback is safe and documented:
   ```powershell
   uv run alembic downgrade -1
   ```
4. Resume operations:
   ```powershell
   curl -X POST http://127.0.0.1:8000/api/v1/emergency/resume -H "Authorization: Bearer <ADMIN_TOKEN>"
   ```

---

## 4. Hard Stop Conditions (Abort Release)

Deployment must immediately halt and abort if any of the following occur:
1. **Live-Money Exposure (§151)**: Any configuration attempting to route transactions to production live payment endpoints without separate executive authorization.
2. **Domain Pollution**: Any external web, ORM, or payment SDK import detected in `apps/api/src/agentpay/domain/`.
3. **Approval Binding Flaw**: Any approval object missing any of the 8 canonical fields (`action_id`, `amount`, `currency`, `merchant_id`, `resource_id`, `action_version`, `principal_id`, `tenant_id`).
4. **Tenant Isolation Breach**: Any repository query executing without an explicit `tenant_id` scope.
5. **Secret Leak**: Any API key, provider secret, or private key detected in application logs or audit ledger payloads.

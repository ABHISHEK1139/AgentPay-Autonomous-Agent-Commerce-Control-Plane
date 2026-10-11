# Contributing to AgentPay

Thank you for your interest in contributing! AgentPay is built as an enterprise-grade control plane where correctness, security boundaries, and determinism take absolute priority.

## 1. Blueprint First

`AGENTS.md` in the repository root is the authoritative architectural and product specification.
Before submitting any change, locate the numbered section that governs your component (see §0.7 Index).
Where code or assumptions conflict with `AGENTS.md`, the blueprint wins. Structural changes require an Architecture Decision Record (ADR) under `docs/decisions/` before modifying code (§82).

## 2. Environment Setup

AgentPay uses Python 3.12+ and `uv` for lightning-fast, hermetic dependency management:

```powershell
# Clone and synchronize the locked virtual environment
uv sync
```

For the Control Center web UI:
```powershell
cd apps/web
npm install
cd ../..
```

## 3. Quality Gates (Must Be 100% Green)

Every pull request must pass all quality gates locally and in CI without exception:

```powershell
uv run ruff format --check .       # Code style & formatting across all Python files
uv run ruff check .                # Strict linter rules
uv run mypy                        # Strict static type checking (0 errors across 167 files)
uv run pytest                      # 748 unit, property, chaos, and live socket network tests
node --test tests/web.test.mjs     # 5 Control Center web suites (ARIA §121 & live API client)
```

### Auxiliary Verification Commands

```powershell
# Adversarial threat harness probes
uv run python tests/adversarial/stress_test_money.py
uv run python tests/adversarial/stress_test_webhooks.py
uv run python tests/adversarial/stress_test_protocols.py

# Cryptographic chain & supply chain verification
uv run python scripts/verify_audit_chain.py --help
uv run python scripts/generate_sbom.py --verify
```

## 4. Definition of Done (§77)

A contribution is considered complete only when:
1. **Fully Tested**: New functionality has unit tests, boundary tests, and (if applicable) property or chaos tests.
2. **Zero Regressions**: All 748+ tests pass cleanly. Never delete or weaken a test to get a green build (§78).
3. **Strict Domain Purity**: The domain layer (`apps/api/src/agentpay/domain/`) must remain pure Python and import zero frameworks or infrastructure ports (§10). Enforced by AST tests.
4. **Multi-Tenant Isolation**: Every repository and application service operation takes an explicit `tenant_id` (§49–§51).
5. **Fail-Closed Semantics**: If money, price, inventory, or permissions are uncertain, fail closed (§2.4).
6. **Documented**: Relevant documentation in `docs/` or `BUGS.md` is updated.

## 5. Commit Standards

We use conventional commit messages:
- `feat(component): add feature description`
- `fix(component): fix bug description`
- `test(component): add regression test`
- `docs(component): update documentation`
- `security(component): harden security boundary`
- `refactor(component): refactor without behavioral changes`

Please keep commits small, single-purpose, and clearly documented.

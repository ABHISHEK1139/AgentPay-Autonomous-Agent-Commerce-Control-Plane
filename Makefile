# AgentPay task runner. Every target delegates to the verified commands in AGENTS.md §0.2.
# Windows setup: winget install ezwinports.make ; winget install Casey.Just

.PHONY: all format lint typecheck test verify web-test web-build adversarial sbom audit audit-chain sync

all: format lint typecheck test

format:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

web-test:
	node --test tests/web.test.mjs

web-build:
	npm --prefix apps/web run build

verify: all web-test sbom

adversarial:
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

sbom:
	uv run python scripts/generate_sbom.py --verify

audit:
	uv run python scripts/dependency_audit.py

audit-chain:
	uv run python scripts/verify_audit_chain.py --help

sync:
	uv sync --frozen

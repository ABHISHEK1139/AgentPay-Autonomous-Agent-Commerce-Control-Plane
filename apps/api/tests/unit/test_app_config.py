"""Unit tests for AgentPay application configuration and factory wiring (§11, §12, §151, AP-005).

Validates:
- Fail-closed parsing of environment variables into immutable AppConfig dataclass.
- Rejection of unknown modes, live payments (§151), and placeholder sandbox credentials.
- Guarding of demo-data seeding across environment levels.
- Immutability and with_overrides behavior.
- Factory dynamic instantiation of payment providers and decision engines.
- Health endpoint mode reporting and safe diagnostics capability summaries with zero secret leakage.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile

import pytest

from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.adapters.mock.mock_payment_provider import MockPaymentProvider
from agentpay.adapters.model_gateway.model_gateway import ModelGateway
from agentpay.adapters.razorpay_payment_provider import InMemoryRazorpaySimulator
from agentpay.adapters.stripe_payment_provider import InMemoryStripeSimulator
from agentpay.api.factory import create_app
from agentpay.config.config import (
    AppConfig,
    ConfigValidationError,
    DecisionEngineType,
    Environment,
    LivePaymentDisabledError,
    PaymentProviderType,
)


def test_default_local_config() -> None:
    """Empty environment defaults to local, mock provider, mock decision engine, demo seeding enabled."""
    config = AppConfig.from_env({})
    assert config.environment == Environment.LOCAL
    assert config.payment_provider == PaymentProviderType.MOCK
    assert config.decision_engine == DecisionEngineType.MOCK
    assert config.seed_demo_data is True
    assert config.allow_unsafe_demo_seed is False
    assert config.tenant_id == "tenant_01"


def test_explicit_development_mode() -> None:
    """Development environment permits demo seeding only when unsafe override is active."""
    env = {
        "AGENTPAY_ENVIRONMENT": "development",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "true",
        "AGENTPAY_SEED_DEMO_DATA": "true",
    }
    config = AppConfig.from_env(env)
    assert config.environment == Environment.DEVELOPMENT
    assert config.seed_demo_data is True
    assert config.allow_unsafe_demo_seed is True


def test_valid_sandbox_config() -> None:
    """Valid sandbox credentials construct AppConfig cleanly."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_ID": "rzp_test_real12345",
        "RAZORPAY_KEY_SECRET": "sec_real_secret_32bytes_value",
        "AGENTPAY_WEBHOOK_SECRET": "whsec_sandbox_explicit_secret_32bytes",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    config = AppConfig.from_env(env)
    assert config.environment == Environment.SANDBOX
    assert config.payment_provider == PaymentProviderType.RAZORPAY_SANDBOX
    assert config.razorpay_key_id == "rzp_test_real12345"
    assert config.razorpay_key_secret == "sec_real_secret_32bytes_value"
    assert config.seed_demo_data is False


def test_sandbox_missing_key_id_fails() -> None:
    """Sandbox mode with razorpay_sandbox provider fails fast if key_id is missing."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_SECRET": "sec_real_secret_32bytes_value",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    with pytest.raises(ConfigValidationError, match="RAZORPAY_KEY_ID is missing or a placeholder"):
        AppConfig.from_env(env)


def test_sandbox_missing_key_secret_fails() -> None:
    """Sandbox mode with razorpay_sandbox provider fails fast if key_secret is missing."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_ID": "rzp_test_real12345",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    with pytest.raises(
        ConfigValidationError, match="RAZORPAY_KEY_SECRET is missing or a placeholder"
    ):
        AppConfig.from_env(env)


def test_sandbox_placeholder_key_id_fails() -> None:
    """Sandbox mode rejects placeholder substrings in key_id."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_ID": "rzp_test_placeholder_id",
        "RAZORPAY_KEY_SECRET": "sec_real_secret_32bytes_value",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    with pytest.raises(ConfigValidationError, match="placeholder"):
        AppConfig.from_env(env)


def test_sandbox_placeholder_key_secret_fails() -> None:
    """Sandbox mode rejects placeholder substrings in key_secret."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_ID": "rzp_test_real12345",
        "RAZORPAY_KEY_SECRET": "your_secret_here_replace_me",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    with pytest.raises(ConfigValidationError, match="placeholder"):
        AppConfig.from_env(env)


def test_sandbox_missing_webhook_secret_fails() -> None:
    """Sandbox mode fails closed when the webhook signing secret is unset (§61)."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
        "RAZORPAY_KEY_ID": "rzp_test_real12345",
        "RAZORPAY_KEY_SECRET": "sec_real_secret_32bytes_value",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    with pytest.raises(ConfigValidationError, match="RAZORPAY_WEBHOOK_SECRET"):
        AppConfig.from_env(env)


def test_webhook_handler_fails_closed_without_secret() -> None:
    """Webhook endpoints fail closed (500, explicit code) when no secret is configured (§61)."""
    from test_purchase_flow import setup_orchestrator

    from agentpay.api.app import AgentPayApiApp

    import time

    orch, *_ = setup_orchestrator(tenant_id="tenant_01")
    app = AgentPayApiApp(orchestrator=orch, webhook_secret=None)
    assert app.webhook_secret is None

    status_code, body = app.handle_request(
        "POST",
        "/api/v1/webhooks/payments",
        headers={
            "x-webhook-signature": "deadbeef",
            "x-webhook-timestamp": str(int(time.time())),
        },
        body={"event_id": "evt_no_secret_01"},
    )
    assert status_code == 500
    assert body["error"]["code"] == "WEBHOOK_SECRET_NOT_CONFIGURED"


def test_invalid_environment_string_fails() -> None:
    """Unknown environment string fails closed with ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="Invalid AGENTPAY_ENVIRONMENT"):
        AppConfig.from_env({"AGENTPAY_ENVIRONMENT": "staging"})


def test_invalid_payment_provider_string_fails() -> None:
    """Unknown payment provider string fails closed with ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="Invalid AGENTPAY_PAYMENT_PROVIDER"):
        AppConfig.from_env({"AGENTPAY_PAYMENT_PROVIDER": "paypal"})


def test_invalid_decision_engine_string_fails() -> None:
    """Unknown decision engine string fails closed with ConfigValidationError."""
    with pytest.raises(ConfigValidationError, match="Invalid AGENTPAY_DECISION_ENGINE"):
        AppConfig.from_env({"AGENTPAY_DECISION_ENGINE": "unsupported_ai"})


def test_live_payment_provider_fails_closed() -> None:
    """Live payment provider mode fails closed unconditionally (§151)."""
    with pytest.raises(LivePaymentDisabledError, match="Live payments are disabled by default"):
        AppConfig.from_env({"AGENTPAY_PAYMENT_PROVIDER": "live"})


def test_razorpay_live_provider_fails_closed() -> None:
    """razorpay_live provider mode fails closed unconditionally (§151)."""
    with pytest.raises(LivePaymentDisabledError, match="Live payments are disabled by default"):
        AppConfig.from_env({"AGENTPAY_PAYMENT_PROVIDER": "razorpay_live"})


def test_stripe_live_provider_fails_closed() -> None:
    """stripe_live provider mode fails closed unconditionally (§151)."""
    with pytest.raises(LivePaymentDisabledError, match="Live payments are disabled by default"):
        AppConfig.from_env({"AGENTPAY_PAYMENT_PROVIDER": "stripe_live"})


def test_demo_seed_rejected_in_sandbox_without_override() -> None:
    """Sandbox environment rejects demo seeding unless unsafe override is provided."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_SEED_DEMO_DATA": "true",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "false",
    }
    with pytest.raises(ConfigValidationError, match="Demo data seeding is rejected in 'sandbox'"):
        AppConfig.from_env(env)


def test_demo_seed_allowed_in_sandbox_with_override() -> None:
    """Sandbox environment allows demo seeding if unsafe override is explicitly enabled."""
    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_SEED_DEMO_DATA": "true",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "true",
        "AGENTPAY_WEBHOOK_SECRET": "whsec_sandbox_explicit_signing_secret",
    }
    config = AppConfig.from_env(env)
    assert config.environment == Environment.SANDBOX
    assert config.seed_demo_data is True
    assert config.allow_unsafe_demo_seed is True


def test_demo_seed_strictly_rejected_in_production() -> None:
    """Production environment rejects demo seeding even if unsafe override is set."""
    env = {
        "AGENTPAY_ENVIRONMENT": "production",
        "AGENTPAY_SEED_DEMO_DATA": "true",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "true",
    }
    with pytest.raises(ConfigValidationError, match="strictly prohibited in production"):
        AppConfig.from_env(env)


def test_app_config_immutability() -> None:
    """AppConfig is a frozen dataclass preventing mutation."""
    config = AppConfig.from_env({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.environment = Environment.SANDBOX  # type: ignore[misc]


def test_config_with_overrides() -> None:
    """with_overrides returns a new validated instance preserving unchanged fields."""
    orig = AppConfig.from_env({})
    modified = orig.with_overrides(tenant_id="tenant_custom_99")
    assert modified.tenant_id == "tenant_custom_99"
    assert orig.tenant_id == "tenant_01"
    assert modified.environment == orig.environment


def test_factory_instantiates_mock_provider() -> None:
    """create_app instantiates MockPaymentProvider and MockDecisionEngine by default."""
    config = AppConfig(
        payment_provider=PaymentProviderType.MOCK,
        decision_engine=DecisionEngineType.MOCK,
    )
    app = create_app(config=config)
    assert isinstance(app.orchestrator._payment_provider, MockPaymentProvider)
    assert isinstance(app.orchestrator._decision_engine, MockDecisionEngine)


def test_factory_instantiates_in_memory_simulators() -> None:
    """create_app instantiates InMemory simulators when configured."""
    rzp_config = AppConfig(payment_provider=PaymentProviderType.IN_MEMORY_RAZORPAY)
    app_rzp = create_app(config=rzp_config)
    assert isinstance(app_rzp.orchestrator._payment_provider, InMemoryRazorpaySimulator)

    stripe_config = AppConfig(payment_provider=PaymentProviderType.IN_MEMORY_STRIPE)
    app_stripe = create_app(config=stripe_config)
    assert isinstance(app_stripe.orchestrator._payment_provider, InMemoryStripeSimulator)


def test_factory_instantiates_model_gateway() -> None:
    """create_app instantiates ModelGateway when decision_engine is model_gateway."""
    config = AppConfig(decision_engine=DecisionEngineType.MODEL_GATEWAY)
    app = create_app(config=config)
    assert isinstance(app.orchestrator._decision_engine, ModelGateway)


def test_factory_explicit_provider_override() -> None:
    """create_app respects explicitly injected payment provider instances."""
    custom_mock = MockPaymentProvider()
    app = create_app(payment_provider=custom_mock)
    assert app.orchestrator._payment_provider is custom_mock


def test_factory_backward_compatibility() -> None:
    """Legacy keyword arguments to create_app are cleanly mapped to AppConfig."""
    app = create_app(
        database_url=None,
        seed_demo_data=True,
        tenant_id="tenant_01",
        webhook_secret="whsec_custom_secret_32bytes_value",
    )
    assert app.config is not None
    assert app.config.tenant_id == "tenant_01"
    assert app.config.razorpay_webhook_secret == "whsec_custom_secret_32bytes_value"


def test_health_endpoint_surfaces_mode_names() -> None:
    """GET /api/v1/health exposes environment, payment_provider, and decision_engine mode names."""
    config = AppConfig(
        environment=Environment.LOCAL,
        payment_provider=PaymentProviderType.MOCK,
        decision_engine=DecisionEngineType.MOCK,
    )
    app = create_app(config=config)
    status_code, body = app.handle_request("GET", "/api/v1/health", headers={})
    assert status_code == 200
    assert body["status"] == "healthy"
    assert body["environment"] == "local"
    assert body["payment_provider"] == "mock"
    assert body["decision_engine"] == "mock"
    assert "version" in body


def test_system_config_safe_summary() -> None:
    """GET /api/v1/system/config returns capability summary without secret leakage."""
    config = AppConfig(
        environment=Environment.LOCAL,
        payment_provider=PaymentProviderType.MOCK,
        decision_engine=DecisionEngineType.MOCK,
    )
    app = create_app(config=config)
    status_code, body = app.handle_request("GET", "/api/v1/system/config", headers={})
    assert status_code == 200
    assert body["environment"] == "local"
    assert body["payment_provider"] == "mock"
    assert body["decision_engine"] == "mock"
    assert body["persistence"] == "in_memory"
    assert body["demo_data_seeded"] is True
    assert body["capabilities"]["payments_live"] is False


def test_zero_secret_leakage_in_diagnostics() -> None:
    """Ensure sensitive credentials never appear in serialized health or config output."""
    raw_secret_rzp = "sec_test_rzp_sensitive_secret_value_12345"
    raw_secret_stripe = "sk_test_stripe_sensitive_key_value_12345"
    raw_key_id = "rzp_test_real123456"

    config = AppConfig(
        environment=Environment.LOCAL,
        payment_provider=PaymentProviderType.MOCK,
        decision_engine=DecisionEngineType.MOCK,
        razorpay_key_id=raw_key_id,
        razorpay_key_secret=raw_secret_rzp,
        stripe_api_key=raw_secret_stripe,
    )
    app = create_app(config=config)

    _, health_body = app.handle_request("GET", "/api/v1/health", headers={})
    _, config_body = app.handle_request("GET", "/api/v1/system/config", headers={})

    health_json = json.dumps(health_body)
    config_json = json.dumps(config_body)

    for secret in (raw_secret_rzp, raw_secret_stripe, raw_key_id):
        assert secret not in health_json, f"Secret leaked in /health: {secret}"
        assert secret not in config_json, f"Secret leaked in /system/config: {secret}"


def test_production_rejects_wildcard_cors() -> None:
    """Production mode rejects wildcard CORS ('*') fail-closed (§62, AP-010)."""
    env = {
        "AGENTPAY_ENVIRONMENT": "production",
        "AGENTPAY_SEED_DEMO_DATA": "false",
        "AGENTPAY_CORS_ALLOWED_ORIGINS": "*",
    }
    with pytest.raises(ConfigValidationError, match="Wildcard CORS"):
        AppConfig.from_env(env)


def test_production_accepts_explicit_cors() -> None:
    """Production mode accepts explicit origins and marks is_production_like true."""
    env = {
        "AGENTPAY_ENVIRONMENT": "production",
        "AGENTPAY_SEED_DEMO_DATA": "false",
        "AGENTPAY_CORS_ALLOWED_ORIGINS": "https://control.agentpay.internal,https://app.agentpay.internal",
        "AGENTPAY_WEBHOOK_SECRET": "whsec_prod_explicit_signing_secret",
    }
    cfg = AppConfig.from_env(env)
    assert cfg.environment == Environment.PRODUCTION
    assert cfg.cors_allowed_origins == (
        "https://control.agentpay.internal",
        "https://app.agentpay.internal",
    )
    assert cfg.is_production_like is True


def test_production_fails_on_unmigrated_database() -> None:
    """Production mode refuses to start on empty database without migrations (§49, AP-007)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name

    db_url = f"sqlite:///{db_path}"
    config = AppConfig(
        environment=Environment.PRODUCTION,
        database_url=db_url,
        seed_demo_data=False,
        cors_allowed_origins=("https://control.agentpay.internal",),
        razorpay_webhook_secret="whsec_prod_explicit_signing_secret",
        stripe_webhook_secret="whsec_prod_explicit_signing_secret",
    )
    with pytest.raises(ConfigValidationError, match="Database schema is not ready"):
        create_app(config=config)


def test_production_succeeds_on_migrated_database() -> None:
    """Production mode starts cleanly when database has been migrated with Alembic (§49, AP-007)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name

    db_url = f"sqlite:///{db_path}"
    from alembic import command
    from test_database_migrations import get_alembic_config

    alembic_cfg = get_alembic_config(db_url)
    command.upgrade(alembic_cfg, "head")

    app_cfg = AppConfig(
        environment=Environment.PRODUCTION,
        database_url=db_url,
        seed_demo_data=False,
        cors_allowed_origins=("https://control.agentpay.internal",),
        razorpay_webhook_secret="whsec_prod_explicit_signing_secret",
        stripe_webhook_secret="whsec_prod_explicit_signing_secret",
    )
    app = create_app(config=app_cfg)
    assert app is not None


def test_documented_sandbox_runbook_configuration() -> None:
    """Documented configuration variables in docs/operations/sandbox-runbook.md construct AppConfig cleanly.

    Verifies:
    - AGENTPAY_ENVIRONMENT=sandbox
    - AGENTPAY_PAYMENT_PROVIDER=razorpay (alias normalized to razorpay_sandbox)
    - RAZORPAY_KEY_ID & RAZORPAY_KEY_SECRET with valid rzp_test_ prefixes
    - AGENTPAY_WEBHOOK_SECRET correctly parsed as razorpay webhook secret
    - factory.create_app wires a RazorpaySandboxProvider instance
    """
    from agentpay.adapters.razorpay_sandbox_provider import RazorpaySandboxProvider

    env = {
        "AGENTPAY_ENVIRONMENT": "sandbox",
        "AGENTPAY_PAYMENT_PROVIDER": "razorpay",
        "RAZORPAY_KEY_ID": "rzp_test_documented123",
        "RAZORPAY_KEY_SECRET": "sec_documented_secret_32bytes",
        "AGENTPAY_WEBHOOK_SECRET": "whsec_runbook_documented_secret",
        "AGENTPAY_SEED_DEMO_DATA": "false",
    }
    cfg = AppConfig.from_env(env)
    assert cfg.environment == Environment.SANDBOX
    assert cfg.payment_provider == PaymentProviderType.RAZORPAY_SANDBOX
    assert cfg.razorpay_key_id == "rzp_test_documented123"
    assert cfg.razorpay_key_secret == "sec_documented_secret_32bytes"
    assert cfg.razorpay_webhook_secret == "whsec_runbook_documented_secret"

    app = create_app(config=cfg)
    assert isinstance(app.orchestrator._payment_provider, RazorpaySandboxProvider)
    assert app.razorpay_webhook_secret == "whsec_runbook_documented_secret"

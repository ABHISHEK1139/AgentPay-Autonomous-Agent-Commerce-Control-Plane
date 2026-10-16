"""Validated, Immutable Application Configuration for AgentPay (§11, §12, §151, AP-005).

Provides centralized, fail-closed configuration parsing and validation:
- Enforces explicit runtime modes (local, development, sandbox, production).
- Enforces strict live-payment prohibition under MVP (§151).
- Validates credentials on startup in sandbox mode, failing fast on placeholder values.
- Controls demo data seeding to prevent 2099-expiry accounts in non-local environments.
- Generates safe capability summaries for diagnostics with zero secret leakage.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final


class ConfigError(Exception):
    """Base exception for AgentPay configuration errors."""

    code = "CONFIG_ERROR"


class ConfigValidationError(ConfigError):
    """Raised when application configuration fails validation (§11, §151)."""

    code = "CONFIG_VALIDATION_ERROR"


class LivePaymentDisabledError(ConfigValidationError):
    """Raised when live payment provider is requested while prohibited (§151)."""

    code = "LIVE_PAYMENTS_DISABLED"


class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class PaymentProviderType(StrEnum):
    MOCK = "mock"
    IN_MEMORY_RAZORPAY = "in_memory_razorpay"
    IN_MEMORY_STRIPE = "in_memory_stripe"
    RAZORPAY_SANDBOX = "razorpay_sandbox"
    RAZORPAY = "razorpay"
    STRIPE = "stripe"


class DecisionEngineType(StrEnum):
    MOCK = "mock"
    MODEL_GATEWAY = "model_gateway"
    ORCAROUTER = "orcarouter"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENROUTER = "openrouter"


PLACEHOLDER_SUBSTRINGS: Final[tuple[str, ...]] = (
    "placeholder",
    "change_me",
    "changeme",
    "your_key",
    "your_secret",
    "dummy",
    "sample",
    "replace_me",
    "test_key",
    "test_secret",
    "xxx",
    "0000",
)


def is_placeholder_secret(value: str | None) -> bool:
    """Return True if value is None, whitespace-only, or contains a placeholder substring."""
    if not value or not value.strip():
        return True
    val_lower = value.strip().lower()
    return any(p in val_lower for p in PLACEHOLDER_SUBSTRINGS)


def _parse_bool(val: str | None, default: bool = False) -> bool:
    """Parse string representation of boolean safely."""
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Immutable, validated configuration for the AgentPay control plane (§11, §12, AP-005)."""

    environment: Environment = Environment.LOCAL
    payment_provider: PaymentProviderType = PaymentProviderType.MOCK
    decision_engine: DecisionEngineType = DecisionEngineType.MOCK
    database_url: str | None = None
    seed_demo_data: bool = True
    allow_unsafe_demo_seed: bool = False
    tenant_id: str = "tenant_01"

    # Razorpay credentials
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str = "whsec_agentpay_default_secure_secret_32bytes"

    # Stripe credentials
    stripe_api_key: str | None = None
    stripe_webhook_secret: str = "whsec_agentpay_default_secure_secret_32bytes"

    # Security & Observability
    require_observability_auth: bool = False
    log_level: str = "INFO"
    cors_allowed_origins: tuple[str, ...] = ("*",)

    # Model / AI gateway credentials, OrcaRouter & OpenRouter (§23)
    llm_api_key: str | None = None
    orcarouter_api_key: str | None = None
    orcarouter_base_url: str = "https://api.orcarouter.ai/v1"
    orcarouter_model: str = "orcarouter/auto"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "apodex/apodex-1.1-mini:free"

    # Provider-neutral OpenAI-compatible model endpoint, including local Ollama (§23)
    model_api_key: str | None = None
    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = "llama3.2"
    model_provider_name: str = "openai-compatible"
    model_allow_private_network: bool = False

    # Live payments safety flag (§151)
    live_payments_enabled: bool = False

    @property
    def is_production_like(self) -> bool:
        """Return True if environment is sandbox or production (§11, §151)."""
        return self.environment in (Environment.SANDBOX, Environment.PRODUCTION)

    def __post_init__(self) -> None:
        """Validate invariant contracts fail-closed (§2.4, §151, AP-005)."""
        # 1. Reject live payments unconditionally in MVP (§151)
        if self.live_payments_enabled or str(self.payment_provider).lower() in (
            "live",
            "razorpay_live",
            "stripe_live",
        ):
            raise LivePaymentDisabledError(
                "Live payments are disabled by default. Autonomous live transactions are "
                "prohibited under current milestone (§151)."
            )

        # 2. Demo data seeding validation
        if self.seed_demo_data:
            if self.environment == Environment.PRODUCTION:
                raise ConfigValidationError(
                    "Demo data seeding with 2099-expiry credentials is strictly prohibited "
                    "in production environment."
                )
            if (
                self.environment in (Environment.SANDBOX, Environment.DEVELOPMENT)
                and not self.allow_unsafe_demo_seed
            ):
                raise ConfigValidationError(
                    f"Demo data seeding is rejected in '{self.environment.value}' environment "
                    f"unless AGENTPAY_UNSAFE_ALLOW_DEMO_SEED=true is explicitly set."
                )

        # 3. Sandbox credential validation
        if (
            self.payment_provider
            in (PaymentProviderType.RAZORPAY_SANDBOX, PaymentProviderType.RAZORPAY)
            and self.environment == Environment.SANDBOX
        ) or self.payment_provider == PaymentProviderType.RAZORPAY_SANDBOX:
            if is_placeholder_secret(self.razorpay_key_id):
                raise ConfigValidationError(
                    "RAZORPAY_KEY_ID is missing or a placeholder string in sandbox mode."
                )
            if is_placeholder_secret(self.razorpay_key_secret):
                raise ConfigValidationError(
                    "RAZORPAY_KEY_SECRET is missing or a placeholder string in sandbox mode."
                )
            k_id = self.razorpay_key_id.strip() if self.razorpay_key_id else ""
            k_sec = self.razorpay_key_secret.strip() if self.razorpay_key_secret else ""
            if not k_id.startswith("rzp_test_"):
                raise ConfigValidationError(
                    f"RAZORPAY_KEY_ID must begin with 'rzp_test_', got '{k_id[:10]}...'"
                )
            if len(k_id) < 14:
                raise ConfigValidationError("RAZORPAY_KEY_ID length must be at least 14 chars")
            if len(k_sec) < 16:
                raise ConfigValidationError("RAZORPAY_KEY_SECRET length must be at least 16 chars")

        # 4. CORS configuration validation in production (§62, AP-010)
        if self.environment == Environment.PRODUCTION and (
            not self.cors_allowed_origins or "*" in self.cors_allowed_origins
        ):
            raise ConfigValidationError(
                "Wildcard CORS ('*') is prohibited in production mode. Set AGENTPAY_CORS_ALLOWED_ORIGINS to explicit origins (§62, AP-010)."
            )

        # 5. Advisory AI / OrcaRouter credential validation (§23)
        if self.decision_engine == DecisionEngineType.ORCAROUTER and is_placeholder_secret(
            self.orcarouter_api_key
        ):
            raise ConfigValidationError(
                "ORCAROUTER_API_KEY is missing or a placeholder string when decision_engine is "
                "'orcarouter'."
            )

        # 6. Advisory AI / OpenRouter credential validation (§23)
        if self.decision_engine == DecisionEngineType.OPENROUTER and is_placeholder_secret(
            self.openrouter_api_key
        ):
            raise ConfigValidationError(
                "OPENROUTER_API_KEY is missing or a placeholder string when decision_engine is 'openrouter'."
            )
        if self.decision_engine == DecisionEngineType.OPENAI_COMPATIBLE and is_placeholder_secret(
            self.model_api_key
        ):
            raise ConfigValidationError(
                "AGENTPAY_MODEL_API_KEY is missing or a placeholder string when decision_engine "
                "is 'openai_compatible'."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AppConfig:
        """Parse and validate configuration from environment variables once at startup."""
        e = env if env is not None else os.environ

        # 1. Environment
        raw_env = e.get("AGENTPAY_ENVIRONMENT", "local").strip().lower()
        try:
            environment = Environment(raw_env)
        except ValueError as exc:
            raise ConfigValidationError(
                f"Invalid AGENTPAY_ENVIRONMENT '{raw_env}'. Must be one of: "
                f"{[m.value for m in Environment]}"
            ) from exc

        # 2. Payment Provider
        raw_provider = e.get("AGENTPAY_PAYMENT_PROVIDER", "mock").strip().lower()
        if raw_provider in ("live", "razorpay_live", "stripe_live"):
            raise LivePaymentDisabledError(
                "Live payments are disabled by default. Autonomous live transactions are "
                "prohibited under current milestone (§151)."
            )
        normalized_provider = raw_provider
        if raw_provider == "razorpay":
            if environment == Environment.SANDBOX:
                normalized_provider = "razorpay_sandbox"
            else:
                normalized_provider = "in_memory_razorpay"
        elif raw_provider == "stripe":
            normalized_provider = "in_memory_stripe"

        try:
            payment_provider = PaymentProviderType(normalized_provider)
        except ValueError as exc:
            raise ConfigValidationError(
                f"Invalid AGENTPAY_PAYMENT_PROVIDER '{raw_provider}'. Must be one of: "
                f"{[p.value for p in PaymentProviderType]} or aliases ['razorpay', 'stripe']"
            ) from exc

        # 3. Decision Engine
        raw_decision = e.get("AGENTPAY_DECISION_ENGINE", "mock").strip().lower()
        try:
            decision_engine = DecisionEngineType(raw_decision)
        except ValueError as exc:
            raise ConfigValidationError(
                f"Invalid AGENTPAY_DECISION_ENGINE '{raw_decision}'. Must be one of: "
                f"{[d.value for d in DecisionEngineType]}"
            ) from exc

        # 4. Persistence & Seeding
        db_url = e.get("DATABASE_URL") or e.get("AGENTPAY_DATABASE_URL")
        default_seed = environment in (Environment.LOCAL, Environment.DEVELOPMENT)
        seed_demo = _parse_bool(e.get("AGENTPAY_SEED_DEMO_DATA"), default=default_seed)
        allow_unsafe_seed = _parse_bool(e.get("AGENTPAY_UNSAFE_ALLOW_DEMO_SEED"), default=False)
        tenant_id = e.get("AGENTPAY_TENANT_ID", "tenant_01").strip() or "tenant_01"

        # 5. Credentials
        rzp_key_id = e.get("RAZORPAY_KEY_ID")
        rzp_key_secret = e.get("RAZORPAY_KEY_SECRET")
        rzp_wh_secret = (
            e.get("RAZORPAY_WEBHOOK_SECRET")
            or e.get("AGENTPAY_WEBHOOK_SECRET")
            or "whsec_agentpay_default_secure_secret_32bytes"
        )
        stripe_key = e.get("STRIPE_API_KEY")
        stripe_wh_secret = (
            e.get("STRIPE_WEBHOOK_SECRET")
            or e.get("AGENTPAY_WEBHOOK_SECRET")
            or "whsec_agentpay_default_secure_secret_32bytes"
        )

        # 6. Observability & AI
        default_obs = environment in (Environment.PRODUCTION, Environment.SANDBOX)
        req_obs = _parse_bool(e.get("AGENTPAY_REQUIRE_OBSERVABILITY_AUTH"), default=default_obs)
        log_level = e.get("AGENTPAY_LOG_LEVEL", "INFO").strip().upper()
        raw_cors = e.get("AGENTPAY_CORS_ALLOWED_ORIGINS")
        if raw_cors:
            cors_origins = tuple(x.strip() for x in raw_cors.split(",") if x.strip())
        elif environment == Environment.PRODUCTION:
            cors_origins = ()
        else:
            cors_origins = ("*",)
        llm_key = e.get("LLM_API_KEY")
        orcarouter_key = e.get("ORCAROUTER_API_KEY") or llm_key
        orcarouter_base = (e.get("ORCAROUTER_BASE_URL") or "https://api.orcarouter.ai/v1").strip()
        orcarouter_model = (e.get("ORCAROUTER_MODEL") or "orcarouter/auto").strip()
        openrouter_key = e.get("OPENROUTER_API_KEY") or llm_key
        openrouter_base = (e.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip()
        openrouter_model = (e.get("OPENROUTER_MODEL") or "apodex/apodex-1.1-mini:free").strip()
        model_key = e.get("AGENTPAY_MODEL_API_KEY") or e.get("LLM_API_KEY")
        model_base = (e.get("AGENTPAY_MODEL_BASE_URL") or "http://127.0.0.1:11434/v1").strip()
        model_name = (e.get("AGENTPAY_MODEL_NAME") or "llama3.2").strip()
        model_provider_name = (e.get("AGENTPAY_MODEL_PROVIDER_NAME") or "openai-compatible").strip()
        model_allow_private_network = _parse_bool(
            e.get("AGENTPAY_MODEL_ALLOW_PRIVATE_NETWORK"), default=False
        )

        # 7. Live safety flag (§151)
        live_flag = _parse_bool(
            e.get("AGENTPAY_LIVE_PAYMENTS_ENABLED") or e.get("LIVE_PAYMENTS_ENABLED"),
            default=False,
        )

        return cls(
            environment=environment,
            payment_provider=payment_provider,
            decision_engine=decision_engine,
            database_url=db_url,
            seed_demo_data=seed_demo,
            allow_unsafe_demo_seed=allow_unsafe_seed,
            tenant_id=tenant_id,
            razorpay_key_id=rzp_key_id,
            razorpay_key_secret=rzp_key_secret,
            razorpay_webhook_secret=rzp_wh_secret,
            stripe_api_key=stripe_key,
            stripe_webhook_secret=stripe_wh_secret,
            require_observability_auth=req_obs,
            log_level=log_level,
            cors_allowed_origins=cors_origins,
            llm_api_key=llm_key,
            orcarouter_api_key=orcarouter_key,
            orcarouter_base_url=orcarouter_base,
            orcarouter_model=orcarouter_model,
            openrouter_api_key=openrouter_key,
            openrouter_base_url=openrouter_base,
            openrouter_model=openrouter_model,
            model_api_key=model_key,
            model_base_url=model_base,
            model_name=model_name,
            model_provider_name=model_provider_name,
            model_allow_private_network=model_allow_private_network,
            live_payments_enabled=live_flag,
        )

    def with_overrides(self, **kwargs: Any) -> AppConfig:
        """Return a new AppConfig with specified field overrides, validating post-init contracts."""
        return replace(self, **kwargs)

    def safe_summary(self) -> dict[str, Any]:
        """Expose safe capability and mode names without leaking secrets (§13, AP-005)."""
        return {
            "environment": self.environment.value,
            "payment_provider": self.payment_provider.value,
            "decision_engine": self.decision_engine.value,
            "persistence": "relational" if self.database_url else "in_memory",
            "demo_data_seeded": self.seed_demo_data,
            "require_observability_auth": self.require_observability_auth,
            "capabilities": {
                "payments_live": False,
                "razorpay_configured": bool(
                    self.razorpay_key_id and not is_placeholder_secret(self.razorpay_key_id)
                ),
                "stripe_configured": bool(
                    self.stripe_api_key and not is_placeholder_secret(self.stripe_api_key)
                ),
                "model_gateway_enabled": self.decision_engine != DecisionEngineType.MOCK,
                "orcarouter_configured": bool(
                    self.orcarouter_api_key and not is_placeholder_secret(self.orcarouter_api_key)
                ),
                "openrouter_configured": bool(
                    self.openrouter_api_key and not is_placeholder_secret(self.openrouter_api_key)
                ),
            },
        }


__all__ = [
    "AppConfig",
    "ConfigError",
    "ConfigValidationError",
    "DecisionEngineType",
    "Environment",
    "LivePaymentDisabledError",
    "PaymentProviderType",
    "is_placeholder_secret",
]

"""Milestone 3 Empirical Sandbox & Runtime Configuration Stress Harness (§10, §39, §41, §43, §60, §61, §151, §152, AP-004, AP-005).

Exhaustively verifies Milestone 3 security invariants across 6 adversarial probes:
1. Probe 1: Sandbox Credential Rejection:
   - RazorpaySandboxProvider and AppConfig reject empty keys, missing secrets, and placeholder
     keys/secrets with clear configuration errors.
2. Probe 2: SSRF Pre-validation in Sandbox Provider:
   - Requests targeting internal/loopback/cloud metadata endpoints (127.0.0.1, 169.254.169.254,
     localhost, http://) are blocked fail-closed before any network call.
3. Probe 3: Network Timeout Mapping (§43):
   - Network timeout in RazorpaySandboxProvider returns PaymentAttempt with
     status=PaymentStatus.UNKNOWN rather than crashing, fabricating a success, or raising an unhandled error.
4. Probe 4: Production Demo Data Seeding Prohibition:
   - Configuring AGENTPAY_ENVIRONMENT=production and seed_demo_data=True fails fast at startup
     with ConfigurationError, even if an unsafe override is attempted.
5. Probe 5: Autonomous Live Payments Prohibition (§151):
   - Configuring live_payments_enabled=True or live provider modes fails fast at startup with
     LivePaymentDisabledError.
6. Probe 6: Diagnostics Secret Leakage Check:
   - Query GET /api/v1/system/config and inspect all returned fields/nested objects.
     Verify that zero secrets, API keys, or webhook secrets appear in the response payload.
7. Probe 7: Refund Invariants & Receipt Length Constraint (§44, AP-004):
   - Verify safe receipt length shortening (<= 40 chars), currency match, balance bounds,
     tenant ownership, and non-positive amount rejection in Razorpay sandbox.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from agentpay.adapters.razorpay_sandbox_provider import (
    RazorpaySandboxConfig,
    RazorpaySandboxCredentialError,
    RazorpaySandboxProvider,
)
from agentpay.api.factory import create_app
from agentpay.config.config import (
    AppConfig,
    ConfigError,
    ConfigValidationError,
    Environment,
    LivePaymentDisabledError,
    PaymentProviderType,
)
from agentpay.domain.errors import InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import PaymentStatus
from agentpay.security.ssrf import SSRFError


def log(msg: str) -> None:
    """Output message to stdout."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def probe_1_sandbox_credential_rejection() -> dict[str, Any]:
    """Probe 1: Verify fail-fast rejection of empty, missing, and placeholder sandbox credentials."""
    log("\n[Probe 1] Testing Sandbox Credential Rejection...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    # 1.1 RazorpaySandboxProvider empty/missing credentials
    bad_provider_credentials: list[tuple[str, str, str]] = [
        ("", "sec_valid_secret_32bytes", "empty key_id"),
        ("   ", "sec_valid_secret_32bytes", "whitespace key_id"),
        ("rzp_test_valid1234", "", "empty key_secret"),
        ("rzp_test_valid1234", "   ", "whitespace key_secret"),
        ("rzp_live_forbidden123", "sec_valid_secret_32bytes", "live key prefix"),
        ("rzp_test_abc", "sec_valid_secret_32bytes", "short key_id (<14 chars)"),
        ("rzp_test_valid1234", "sec_short", "short key_secret (<16 chars)"),
    ]

    for kid, ksec, desc in bad_provider_credentials:
        try:
            RazorpaySandboxProvider(key_id=kid, key_secret=ksec)
            log(f"  [-] FAIL: RazorpaySandboxProvider accepted {desc}")
            results["checks"].append({"test": desc, "status": "FAIL"})
            results["passed"] = False
        except RazorpaySandboxCredentialError:
            results["checks"].append({"test": desc, "status": "PASS"})
        except Exception as exc:
            log(f"  [-] FAIL: Unexpected exception for {desc}: {type(exc).__name__}: {exc}")
            results["checks"].append({"test": desc, "status": "FAIL", "error": str(exc)})
            results["passed"] = False

    # 1.2 RazorpaySandboxProvider placeholder patterns
    placeholders: list[str] = [
        "placeholder",
        "your_key",
        "your_secret",
        "dummy",
        "changeme",
        "change_me",
        "sample",
        "replace_me",
        "test_secret",
        "xxx",
        "0000",
    ]

    for p in placeholders:
        # Placeholder in key_id
        kid_placeholder = f"rzp_test_{p}_12345"
        try:
            RazorpaySandboxProvider(key_id=kid_placeholder, key_secret="sec_valid_secret_32bytes")
            log(f"  [-] FAIL: RazorpaySandboxProvider accepted placeholder key_id with '{p}'")
            results["checks"].append({"test": f"placeholder_key_id_{p}", "status": "FAIL"})
            results["passed"] = False
        except RazorpaySandboxCredentialError:
            results["checks"].append({"test": f"placeholder_key_id_{p}", "status": "PASS"})

        # Placeholder in key_secret
        ksec_placeholder = f"sec_{p}_long_secret_value_32bytes"
        try:
            RazorpaySandboxProvider(key_id="rzp_test_valid1234", key_secret=ksec_placeholder)
            log(f"  [-] FAIL: RazorpaySandboxProvider accepted placeholder key_secret with '{p}'")
            results["checks"].append({"test": f"placeholder_key_sec_{p}", "status": "FAIL"})
            results["passed"] = False
        except RazorpaySandboxCredentialError:
            results["checks"].append({"test": f"placeholder_key_sec_{p}", "status": "PASS"})

    # 1.2a Mixed case and whitespace placeholders
    mixed_case_placeholders: list[tuple[str, str, str]] = [
        ("rzp_test_PLACEHOLDER_99", "sec_valid_secret_32bytes", "uppercase PLACEHOLDER in key_id"),
        ("rzp_test_ChAnGeMe_99", "sec_valid_secret_32bytes", "mixed-case ChAnGeMe in key_id"),
        (
            "   rzp_test_dummy_99   ",
            "sec_valid_secret_32bytes",
            "padded whitespace dummy in key_id",
        ),
        (
            "rzp_test_valid1234",
            "sec_YOUR_SECRET_here_32bytes",
            "uppercase YOUR_SECRET in key_secret",
        ),
        (
            "rzp_test_valid1234",
            "sec_RePLaCe_Me_value_32bytes",
            "mixed-case RePLaCe_Me in key_secret",
        ),
        (
            "rzp_test_valid1234",
            "   changeme_padded_long_32bytes   ",
            "padded whitespace changeme in key_secret",
        ),
    ]

    for kid, ksec, desc in mixed_case_placeholders:
        try:
            RazorpaySandboxProvider(key_id=kid, key_secret=ksec)
            log(f"  [-] FAIL: RazorpaySandboxProvider accepted {desc}")
            results["checks"].append({"test": desc, "status": "FAIL"})
            results["passed"] = False
        except RazorpaySandboxCredentialError:
            results["checks"].append({"test": desc, "status": "PASS"})

    # 1.3 RazorpaySandboxConfig construction via config object
    try:
        cfg = RazorpaySandboxConfig(
            key_id="rzp_test_placeholder", key_secret="your_secret_here_123"
        )
        RazorpaySandboxProvider(config=cfg)
        log("  [-] FAIL: RazorpaySandboxProvider accepted config with placeholder values")
        results["checks"].append({"test": "config_object_placeholder", "status": "FAIL"})
        results["passed"] = False
    except RazorpaySandboxCredentialError:
        results["checks"].append({"test": "config_object_placeholder", "status": "PASS"})

    # 1.4 AppConfig validation for sandbox mode
    bad_app_configs: list[tuple[dict[str, str], str]] = [
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox missing both keys",
        ),
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "RAZORPAY_KEY_ID": "rzp_test_valid1234",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox missing key_secret",
        ),
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "RAZORPAY_KEY_SECRET": "sec_valid_secret_32bytes",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox missing key_id",
        ),
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "RAZORPAY_KEY_ID": "rzp_test_placeholder_id",
                "RAZORPAY_KEY_SECRET": "sec_valid_secret_32bytes",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox placeholder key_id",
        ),
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "RAZORPAY_KEY_ID": "rzp_test_valid1234",
                "RAZORPAY_KEY_SECRET": "your_secret_here_replace_me",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox placeholder key_secret",
        ),
        (
            {
                "AGENTPAY_ENVIRONMENT": "sandbox",
                "AGENTPAY_PAYMENT_PROVIDER": "razorpay_sandbox",
                "RAZORPAY_KEY_ID": "rzp_live_forbidden123",
                "RAZORPAY_KEY_SECRET": "sec_valid_secret_32bytes",
                "AGENTPAY_SEED_DEMO_DATA": "false",
            },
            "AppConfig sandbox non-test prefix key_id",
        ),
    ]

    for env_dict, desc in bad_app_configs:
        try:
            AppConfig.from_env(env_dict)
            log(f"  [-] FAIL: AppConfig accepted {desc}")
            results["checks"].append({"test": desc, "status": "FAIL"})
            results["passed"] = False
        except ConfigValidationError:
            results["checks"].append({"test": desc, "status": "PASS"})
        except Exception as exc:
            log(f"  [-] FAIL: Unexpected exception for {desc}: {type(exc).__name__}: {exc}")
            results["checks"].append({"test": desc, "status": "FAIL", "error": str(exc)})
            results["passed"] = False

    log(
        f"  [+] Probe 1 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_2_ssrf_prevalidation_in_sandbox_provider() -> dict[str, Any]:
    """Probe 2: Verify SSRF pre-validation blocks internal/loopback/cloud metadata fail-closed."""
    log("\n[Probe 2] Testing SSRF Pre-validation in Sandbox Provider...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    dangerous_targets: list[tuple[str, str]] = [
        ("http://api.razorpay.com/v1", "Disallowed plaintext HTTP scheme"),
        ("https://127.0.0.1/v1", "Loopback IPv4 127.0.0.1"),
        ("https://127.0.0.2:8443/v1", "Loopback IPv4 with custom port"),
        ("https://169.254.169.254/v1", "AWS/GCP Cloud Metadata 169.254.169.254"),
        ("https://localhost/v1", "Localhost hostname"),
        ("https://10.0.0.1/v1", "RFC 1918 Class A private IP"),
        ("https://192.168.1.1/v1", "RFC 1918 Class C private IP"),
        ("https://172.16.0.1/v1", "RFC 1918 Class B private IP"),
        ("https://[::1]/v1", "IPv6 Loopback ::1"),
        ("https://[::ffff:127.0.0.1]/v1", "IPv4-mapped IPv6 Loopback"),
        ("https://admin:pass@127.0.0.1/v1", "Userinfo URL component with private target"),
    ]

    for target_url, desc in dangerous_targets:
        # Create provider configured with valid credentials but unsafe base_url
        provider = RazorpaySandboxProvider(
            key_id="rzp_test_valid1234",
            key_secret="sec_valid_secret_32bytes",
            base_url=target_url,
            resolve_dns=False,  # Hermetic local assertion
        )

        # Test create_payment fail-closed SSRF rejection
        try:
            provider.create_payment(
                tenant_id="tenant_01",
                intent_id="intent_01",
                amount=Money(1000, "INR"),
                idempotency_key=f"idem_ssrf_{desc[:10]}",
            )
            log(f"  [-] FAIL: create_payment did not block SSRF target {target_url} ({desc})")
            results["checks"].append({"test": f"create_payment_{desc}", "status": "FAIL"})
            results["passed"] = False
        except SSRFError as ssrf_exc:
            results["checks"].append(
                {"test": f"create_payment_{desc}", "status": "PASS", "code": ssrf_exc.code}
            )
        except Exception as exc:
            log(
                f"  [-] FAIL: create_payment raised non-SSRF exception for {desc}: {type(exc).__name__}: {exc}"
            )
            results["checks"].append(
                {"test": f"create_payment_{desc}", "status": "FAIL", "error": str(exc)}
            )
            results["passed"] = False

        # Test get_payment fail-closed SSRF rejection
        try:
            provider.get_payment(tenant_id="tenant_01", attempt_id="order_12345")
            log(f"  [-] FAIL: get_payment did not block SSRF target {target_url} ({desc})")
            results["checks"].append({"test": f"get_payment_{desc}", "status": "FAIL"})
            results["passed"] = False
        except SSRFError as ssrf_exc:
            results["checks"].append(
                {"test": f"get_payment_{desc}", "status": "PASS", "code": ssrf_exc.code}
            )
        except Exception as exc:
            log(
                f"  [-] FAIL: get_payment raised non-SSRF exception for {desc}: {type(exc).__name__}: {exc}"
            )
            results["checks"].append(
                {"test": f"get_payment_{desc}", "status": "FAIL", "error": str(exc)}
            )
            results["passed"] = False

    log(
        f"  [+] Probe 2 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_3_network_timeout_mapping() -> dict[str, Any]:
    """Probe 3: Verify network timeouts map to PaymentStatus.UNKNOWN (§43) without crash or fabrication."""
    log("\n[Probe 3] Testing Network Timeout Mapping (§43)...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    # 3.1 Simulated network timeout in create_payment via transport
    def timeout_transport(_: Any) -> tuple[int, bytes, dict[str, str]]:
        raise urllib.error.URLError(reason=TimeoutError("Connection timed out to razorpay sandbox"))

    provider_timeout = RazorpaySandboxProvider(
        key_id="rzp_test_valid1234",
        key_secret="sec_valid_secret_32bytes",
        transport=timeout_transport,
    )

    amount = Money(250000, "INR")
    attempt = provider_timeout.create_payment(
        tenant_id="tenant_01",
        intent_id="intent_timeout_test_01",
        amount=amount,
        idempotency_key="idem_timeout_probe_01",
    )

    # Invariants for timeout outcome:
    # 1. Must NOT crash
    # 2. Must NOT fabricate success (CAPTURED or AUTHORIZED)
    # 3. Must be UNKNOWN
    # 4. Must preserve amount, currency, tenant, intent, idempotency_key
    # 5. Must NOT fabricate a provider_payment_id
    if attempt.status == PaymentStatus.UNKNOWN:
        results["checks"].append({"test": "create_payment_status_unknown", "status": "PASS"})
    else:
        log(f"  [-] FAIL: Expected status UNKNOWN on timeout, got {attempt.status}")
        results["checks"].append({"test": "create_payment_status_unknown", "status": "FAIL"})
        results["passed"] = False

    if attempt.provider_payment_id is None:
        results["checks"].append({"test": "create_payment_no_fake_provider_id", "status": "PASS"})
    else:
        log(
            f"  [-] FAIL: Expected provider_payment_id to be None, got {attempt.provider_payment_id}"
        )
        results["checks"].append({"test": "create_payment_no_fake_provider_id", "status": "FAIL"})
        results["passed"] = False

    if attempt.amount == amount and attempt.tenant_id == "tenant_01":
        results["checks"].append(
            {"test": "create_payment_money_tenant_preserved", "status": "PASS"}
        )
    else:
        log("  [-] FAIL: Amount or tenant not preserved in timeout PaymentAttempt")
        results["checks"].append(
            {"test": "create_payment_money_tenant_preserved", "status": "FAIL"}
        )
        results["passed"] = False

    if attempt.failure_reason and "reconciliation required" in attempt.failure_reason.lower():
        results["checks"].append(
            {"test": "create_payment_reconciliation_guidance", "status": "PASS"}
        )
    else:
        log(
            f"  [-] FAIL: Expected reconciliation guidance in failure_reason, got {attempt.failure_reason}"
        )
        results["checks"].append(
            {"test": "create_payment_reconciliation_guidance", "status": "FAIL"}
        )
        results["passed"] = False

    # 3.2 Simulated network timeout in get_payment
    get_attempt = provider_timeout.get_payment(
        tenant_id="tenant_01", attempt_id="order_timeout_rec_99"
    )
    if get_attempt.status == PaymentStatus.UNKNOWN:
        results["checks"].append({"test": "get_payment_status_unknown", "status": "PASS"})
    else:
        log(f"  [-] FAIL: get_payment returned {get_attempt.status} instead of UNKNOWN on timeout")
        results["checks"].append({"test": "get_payment_status_unknown", "status": "FAIL"})
        results["passed"] = False

    # 3.3 Secret redaction in HTTP error responses
    canary_sec = "sec_ultra_sensitive_secret_32bytes"
    canary_kid = "rzp_test_canary_key_12345"

    def leaking_error_transport(_: Any) -> tuple[int, bytes, dict[str, str]]:
        # Simulate upstream provider error reflecting secrets
        err_json = json.dumps(
            {
                "error": {
                    "code": "BAD_REQUEST",
                    "description": f"Authentication failed with key {canary_kid} and secret {canary_sec}",
                }
            }
        ).encode("utf-8")
        import io

        fp = io.BytesIO(err_json)
        raise urllib.error.HTTPError(
            url="https://api.razorpay.com/v1/orders",
            code=400,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=fp,
        )

    provider_redact = RazorpaySandboxProvider(
        key_id=canary_kid,
        key_secret=canary_sec,
        transport=leaking_error_transport,
    )

    from agentpay.domain.errors import InvalidPaymentError

    try:
        provider_redact.create_payment(
            tenant_id="tenant_01",
            intent_id="intent_01",
            amount=Money(5000, "INR"),
            idempotency_key="idem_redact_01",
        )
        log("  [-] FAIL: Expected InvalidPaymentError on HTTP 400 error")
        results["checks"].append({"test": "http_error_raised", "status": "FAIL"})
        results["passed"] = False
    except InvalidPaymentError as exc:
        err_str = str(exc)
        if canary_sec in err_str:
            log(f"  [-] FAIL: Secret {canary_sec} leaked in error string: {err_str}")
            results["checks"].append({"test": "secret_redacted_in_error", "status": "FAIL"})
            results["passed"] = False
        elif "[REDACTED]" in err_str:
            results["checks"].append({"test": "secret_redacted_in_error", "status": "PASS"})
        else:
            results["checks"].append({"test": "secret_redacted_in_error", "status": "PASS"})

    log(
        f"  [+] Probe 3 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_4_production_demo_data_seeding_prohibition() -> dict[str, Any]:
    """Probe 4: Verify production demo data seeding is strictly rejected at startup."""
    log("\n[Probe 4] Testing Production Demo Data Seeding Prohibition...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    # 4.1 Production with seed_demo_data=True without unsafe override
    env_prod_seed = {
        "AGENTPAY_ENVIRONMENT": "production",
        "AGENTPAY_SEED_DEMO_DATA": "true",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "false",
    }
    try:
        AppConfig.from_env(env_prod_seed)
        log("  [-] FAIL: AppConfig permitted demo seeding in production environment")
        results["checks"].append({"test": "prod_seed_no_override", "status": "FAIL"})
        results["passed"] = False
    except (ConfigValidationError, ConfigError) as exc:
        results["checks"].append(
            {"test": "prod_seed_no_override", "status": "PASS", "err": str(exc)}
        )

    # 4.2 Production with seed_demo_data=True WITH unsafe override attempted
    # CRITICAL: Even if an attacker attempts an unsafe override, production must NEVER allow demo seeding!
    env_prod_seed_override = {
        "AGENTPAY_ENVIRONMENT": "production",
        "AGENTPAY_SEED_DEMO_DATA": "true",
        "AGENTPAY_UNSAFE_ALLOW_DEMO_SEED": "true",
    }
    try:
        AppConfig.from_env(env_prod_seed_override)
        log("  [-] FAIL: AppConfig permitted demo seeding in production with unsafe override!")
        results["checks"].append({"test": "prod_seed_with_unsafe_override", "status": "FAIL"})
        results["passed"] = False
    except (ConfigValidationError, ConfigError) as exc:
        results["checks"].append(
            {"test": "prod_seed_with_unsafe_override", "status": "PASS", "err": str(exc)}
        )

    # 4.3 Direct AppConfig dataclass construction
    try:
        AppConfig(
            environment=Environment.PRODUCTION,
            seed_demo_data=True,
            allow_unsafe_demo_seed=True,
        )
        log("  [-] FAIL: Direct AppConfig constructor allowed production demo seeding!")
        results["checks"].append({"test": "direct_app_config_prod_seed", "status": "FAIL"})
        results["passed"] = False
    except (ConfigValidationError, ConfigError):
        results["checks"].append({"test": "direct_app_config_prod_seed", "status": "PASS"})

    # 4.4 create_app factory startup with production environment and seed_demo_data=True
    try:
        create_app(
            config=AppConfig(
                environment=Environment.PRODUCTION,
                seed_demo_data=False,
            ).with_overrides(seed_demo_data=True, allow_unsafe_demo_seed=True)
        )
        log("  [-] FAIL: create_app booted with production demo seeding enabled!")
        results["checks"].append({"test": "create_app_prod_seed", "status": "FAIL"})
        results["passed"] = False
    except (ConfigValidationError, ConfigError):
        results["checks"].append({"test": "create_app_prod_seed", "status": "PASS"})

    log(
        f"  [+] Probe 4 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_5_autonomous_live_payments_prohibition() -> dict[str, Any]:
    """Probe 5: Verify autonomous live payments are strictly prohibited at startup (§151)."""
    log("\n[Probe 5] Testing Autonomous Live Payments Prohibition (§151)...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    # 5.1 live_payments_enabled=True in AppConfig
    try:
        AppConfig(live_payments_enabled=True)
        log("  [-] FAIL: AppConfig permitted live_payments_enabled=True!")
        results["checks"].append({"test": "live_payments_enabled_flag", "status": "FAIL"})
        results["passed"] = False
    except LivePaymentDisabledError as exc:
        results["checks"].append(
            {"test": "live_payments_enabled_flag", "status": "PASS", "err": str(exc)}
        )
    except Exception as exc:
        log(f"  [-] FAIL: Unexpected error for live flag: {type(exc).__name__}: {exc}")
        results["checks"].append({"test": "live_payments_enabled_flag", "status": "FAIL"})
        results["passed"] = False

    # 5.2 AGENTPAY_LIVE_PAYMENTS_ENABLED=true via from_env
    for key in ("AGENTPAY_LIVE_PAYMENTS_ENABLED", "LIVE_PAYMENTS_ENABLED"):
        try:
            AppConfig.from_env({key: "true"})
            log(f"  [-] FAIL: AppConfig.from_env permitted {key}=true!")
            results["checks"].append({"test": f"env_{key}", "status": "FAIL"})
            results["passed"] = False
        except LivePaymentDisabledError:
            results["checks"].append({"test": f"env_{key}", "status": "PASS"})

    # 5.3 Live payment provider names
    for provider_name in ("live", "razorpay_live", "stripe_live"):
        try:
            AppConfig.from_env({"AGENTPAY_PAYMENT_PROVIDER": provider_name})
            log(
                f"  [-] FAIL: AppConfig.from_env permitted AGENTPAY_PAYMENT_PROVIDER={provider_name}!"
            )
            results["checks"].append({"test": f"provider_{provider_name}", "status": "FAIL"})
            results["passed"] = False
        except LivePaymentDisabledError:
            results["checks"].append({"test": f"provider_{provider_name}", "status": "PASS"})

    # 5.4 create_app with live payments enabled
    try:
        create_app(config=AppConfig(seed_demo_data=True).with_overrides(live_payments_enabled=True))
        log("  [-] FAIL: create_app permitted live_payments_enabled=True!")
        results["checks"].append({"test": "create_app_live_payments", "status": "FAIL"})
        results["passed"] = False
    except LivePaymentDisabledError:
        results["checks"].append({"test": "create_app_live_payments", "status": "PASS"})

    log(
        f"  [+] Probe 5 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_6_diagnostics_secret_leakage_check() -> dict[str, Any]:
    """Probe 6: Verify zero secrets leak in GET /api/v1/system/config or /health payloads."""
    log("\n[Probe 6] Testing Diagnostics Secret Leakage Check...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    canary_secrets: dict[str, str] = {
        "rzp_key_id": "rzp_test_canary_012345",
        "rzp_key_secret": "sec_canary_rzp_secret_32bytes_safe",
        "rzp_webhook_secret": "whsec_canary_razorpay_whsec_32bytes",
        "stripe_key": "sk_test_canary_stripe_key_32bytes",
        "stripe_webhook_secret": "whsec_canary_stripe_whsec_32bytes",
        "llm_key": "sk-canary-model-gateway-llm-token-1234",
    }

    config = AppConfig(
        environment=Environment.LOCAL,
        payment_provider=PaymentProviderType.MOCK,
        razorpay_key_id=canary_secrets["rzp_key_id"],
        razorpay_key_secret=canary_secrets["rzp_key_secret"],
        razorpay_webhook_secret=canary_secrets["rzp_webhook_secret"],
        stripe_api_key=canary_secrets["stripe_key"],
        stripe_webhook_secret=canary_secrets["stripe_webhook_secret"],
        llm_api_key=canary_secrets["llm_key"],
    )

    app = create_app(config=config)

    # 6.1 Inspect GET /api/v1/system/config
    status_code, config_body = app.handle_request("GET", "/api/v1/system/config", headers={})
    if status_code != 200:
        log(f"  [-] FAIL: /api/v1/system/config returned HTTP {status_code}")
        results["checks"].append({"test": "system_config_status_200", "status": "FAIL"})
        results["passed"] = False
    else:
        results["checks"].append({"test": "system_config_status_200", "status": "PASS"})

    config_json = json.dumps(config_body)
    for name, secret in canary_secrets.items():
        if secret in config_json:
            log(f"  [-] FAIL: Secret {name} leaked in /api/v1/system/config: {secret}")
            results["checks"].append({"test": f"leak_config_{name}", "status": "FAIL"})
            results["passed"] = False
        else:
            results["checks"].append({"test": f"leak_config_{name}", "status": "PASS"})

    # 6.2 Recursive value inspection of config_body
    def inspect_recursively(obj: Any, path: str = "") -> list[str]:
        findings: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_lower = str(k).lower()
                if any(sec in k_lower for sec in ("secret", "password", "private_key", "token")):
                    findings.append(f"Suspicious key '{path}.{k}'")
                findings.extend(inspect_recursively(v, f"{path}.{k}"))
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                findings.extend(inspect_recursively(item, f"{path}[{idx}]"))
        elif isinstance(obj, str):
            for sname, sval in canary_secrets.items():
                if sval in obj:
                    findings.append(f"Secret '{sname}' present in value at '{path}'")
        return findings

    recursive_leaks = inspect_recursively(config_body, "system_config")
    if recursive_leaks:
        log(f"  [-] FAIL: Found sensitive keys/values in config: {recursive_leaks}")
        results["checks"].append(
            {"test": "recursive_inspection", "status": "FAIL", "leaks": recursive_leaks}
        )
        results["passed"] = False
    else:
        results["checks"].append({"test": "recursive_inspection", "status": "PASS"})

    # 6.3 Inspect GET /api/v1/health
    h_status, health_body = app.handle_request("GET", "/api/v1/health", headers={})
    assert h_status == 200
    health_json = json.dumps(health_body)
    for name, secret in canary_secrets.items():
        if secret in health_json:
            log(f"  [-] FAIL: Secret {name} leaked in /api/v1/health: {secret}")
            results["checks"].append({"test": f"leak_health_{name}", "status": "FAIL"})
            results["passed"] = False
        else:
            results["checks"].append({"test": f"leak_health_{name}", "status": "PASS"})

    # 6.4 Verify safe boolean capability flags are present
    caps = config_body.get("capabilities", {})
    if caps.get("payments_live") is False and caps.get("razorpay_configured") is True:
        results["checks"].append({"test": "safe_capabilities_flags", "status": "PASS"})
    else:
        log(f"  [-] FAIL: Expected safe capability flags, got {caps}")
        results["checks"].append({"test": "safe_capabilities_flags", "status": "FAIL"})
        results["passed"] = False

    log(
        f"  [+] Probe 6 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def probe_7_refund_validation_and_safe_receipt() -> dict[str, Any]:
    """Probe 7: Verify Razorpay Sandbox refund invariants and receipt length safety (§44, AP-004)."""
    log("\n[Probe 7] Testing Refund Invariants and Safe Receipt Length (§44, AP-004)...")
    results: dict[str, Any] = {"checks": [], "passed": True}

    recorded_payloads: list[dict[str, Any]] = []

    def mock_transport(req: Any) -> tuple[int, bytes, dict[str, str]]:
        method = req.get_method()
        url = req.full_url
        if method == "POST" and "/orders" in url:
            body = json.loads(req.data.decode("utf-8")) if hasattr(req, "data") and req.data else {}
            recorded_payloads.append(body)
            return (
                201,
                json.dumps(
                    {
                        "id": "order_test_rzp_12345",
                        "status": "created",
                        "amount": body.get("amount", 10000),
                        "currency": body.get("currency", "INR"),
                    }
                ).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        if method == "GET" and "/payments/" in url:
            return (
                200,
                json.dumps(
                    {
                        "id": "pay_test_rzp_67890",
                        "amount": 10000,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {"tenant_id": "tenant_01", "intent_id": "intent_test_01"},
                    }
                ).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        if method == "POST" and "/refund" in url:
            body = json.loads(req.data.decode("utf-8")) if hasattr(req, "data") and req.data else {}
            return (
                200,
                json.dumps(
                    {
                        "id": "rfnd_test_rzp_99999",
                        "amount": body.get("amount", 10000),
                        "currency": "INR",
                    }
                ).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        return 404, b"{}", {}

    provider = RazorpaySandboxProvider(
        key_id="rzp_test_valid1234",
        key_secret="sec_valid_secret_32bytes",
        transport=mock_transport,
    )

    # 7.1 Safe receipt length constraint (idempotency key > 40 chars)
    long_idem_key = "idem_very_long_composite_idempotency_key_exceeding_forty_characters_123456789"
    assert len(long_idem_key) > 40
    provider.create_payment("tenant_01", "intent_01", Money(10000, "INR"), long_idem_key)
    last_payload = recorded_payloads[-1]
    receipt = str(last_payload.get("receipt", ""))
    if len(receipt) <= 40 and receipt.startswith("rcpt_"):
        results["checks"].append({"test": "safe_receipt_length_shortened", "status": "PASS"})
    else:
        log(f"  [-] FAIL: receipt length {len(receipt)} > 40 or missing rcpt_ prefix: '{receipt}'")
        results["checks"].append({"test": "safe_receipt_length_shortened", "status": "FAIL"})
        results["passed"] = False

    # 7.2 Short idempotency key (<= 40 chars) preserved as receipt
    short_idem_key = "idem_short_key_25_chars"
    provider.create_payment("tenant_01", "intent_01", Money(10000, "INR"), short_idem_key)
    last_payload = recorded_payloads[-1]
    if last_payload.get("receipt") == short_idem_key:
        results["checks"].append({"test": "short_receipt_preserved", "status": "PASS"})
    else:
        log(f"  [-] FAIL: short receipt not preserved: '{last_payload.get('receipt')}'")
        results["checks"].append({"test": "short_receipt_preserved", "status": "FAIL"})
        results["passed"] = False

    # 7.3 Currency mismatch rejection
    try:
        provider.refund("tenant_01", "pay_test_rzp_67890", Money(5000, "USD"), "wrong currency")
        log("  [-] FAIL: refund accepted mismatched currency (USD vs INR)")
        results["checks"].append({"test": "refund_currency_mismatch", "status": "FAIL"})
        results["passed"] = False
    except InvalidPaymentError as exc:
        if "does not match payment currency" in str(exc):
            results["checks"].append({"test": "refund_currency_mismatch", "status": "PASS"})
        else:
            log(f"  [-] FAIL: unexpected error message: {exc}")
            results["checks"].append({"test": "refund_currency_mismatch", "status": "FAIL"})
            results["passed"] = False

    # 7.4 Amount exceeds payment amount rejection
    try:
        provider.refund("tenant_01", "pay_test_rzp_67890", Money(20000, "INR"), "over-refund")
        log("  [-] FAIL: refund accepted amount exceeding payment amount")
        results["checks"].append({"test": "refund_amount_exceeded", "status": "FAIL"})
        results["passed"] = False
    except InvalidPaymentError as exc:
        if "exceeds payment amount" in str(exc):
            results["checks"].append({"test": "refund_amount_exceeded", "status": "PASS"})
        else:
            log(f"  [-] FAIL: unexpected error message: {exc}")
            results["checks"].append({"test": "refund_amount_exceeded", "status": "FAIL"})
            results["passed"] = False

    # 7.5 Cross-tenant refund rejection
    try:
        provider.refund("tenant_other", "pay_test_rzp_67890", Money(5000, "INR"), "cross tenant")
        log("  [-] FAIL: refund accepted cross-tenant request")
        results["checks"].append({"test": "refund_cross_tenant", "status": "FAIL"})
        results["passed"] = False
    except InvalidPaymentError as exc:
        if "belongs to tenant" in str(exc):
            results["checks"].append({"test": "refund_cross_tenant", "status": "PASS"})
        else:
            log(f"  [-] FAIL: unexpected error message: {exc}")
            results["checks"].append({"test": "refund_cross_tenant", "status": "FAIL"})
            results["passed"] = False

    # 7.6 Non-positive refund amount rejection
    try:
        provider.refund("tenant_01", "pay_test_rzp_67890", Money(0, "INR"), "zero amount")
        log("  [-] FAIL: refund accepted zero amount")
        results["checks"].append({"test": "refund_non_positive_0", "status": "FAIL"})
        results["passed"] = False
    except InvalidPaymentError as exc:
        if "strictly positive" in str(exc):
            results["checks"].append({"test": "refund_non_positive_0", "status": "PASS"})
        else:
            log(f"  [-] FAIL: unexpected error message: {exc}")
            results["checks"].append({"test": "refund_non_positive_0", "status": "FAIL"})
            results["passed"] = False

    # 7.7 Valid refund succeeds
    try:
        ref = provider.refund(
            "tenant_01", "pay_test_rzp_67890", Money(5000, "INR"), "partial refund"
        )
        if ref.refund_id == "rfnd_test_rzp_99999" and ref.amount == Money(5000, "INR"):
            results["checks"].append({"test": "valid_refund_success", "status": "PASS"})
        else:
            results["checks"].append({"test": "valid_refund_success", "status": "FAIL"})
            results["passed"] = False
    except Exception as exc:
        log(f"  [-] FAIL: valid refund raised unexpected error: {exc}")
        results["checks"].append({"test": "valid_refund_success", "status": "FAIL"})
        results["passed"] = False

    log(
        f"  [+] Probe 7 complete. Checks passed: {len(results['checks'])}, Result: {results['passed']}"
    )
    return results


def main() -> int:
    """Execute all 7 Milestone 3 adversarial stress probes."""
    log("=" * 80)
    log("AGENTPAY MILESTONE 3 (AP-004 & AP-005) ADVERSARIAL STRESS HARNESS")
    log("=" * 80)

    p1 = probe_1_sandbox_credential_rejection()
    p2 = probe_2_ssrf_prevalidation_in_sandbox_provider()
    p3 = probe_3_network_timeout_mapping()
    p4 = probe_4_production_demo_data_seeding_prohibition()
    p5 = probe_5_autonomous_live_payments_prohibition()
    p6 = probe_6_diagnostics_secret_leakage_check()
    p7 = probe_7_refund_validation_and_safe_receipt()

    all_probes = [p1, p2, p3, p4, p5, p6, p7]
    total_checks = sum(len(p["checks"]) for p in all_probes)
    passed_checks = sum(sum(1 for c in p["checks"] if c["status"] == "PASS") for p in all_probes)
    all_passed = all(p["passed"] for p in all_probes)

    log("\n" + "=" * 80)
    log(f"SUMMARY: {passed_checks}/{total_checks} adversarial checks passed.")
    if all_passed:
        log("[+] VERDICT: ALL INVARIANTS SATISFIED -> APPROVE")
        log("=" * 80)
        return 0
    else:
        log("[-] VERDICT: INVARIANT VIOLATIONS DETECTED -> REJECT")
        log("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())

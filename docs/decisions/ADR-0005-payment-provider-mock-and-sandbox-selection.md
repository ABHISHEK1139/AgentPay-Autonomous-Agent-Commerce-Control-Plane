# ADR-0005: Explicit Payment Provider Runtime Selection and Sandbox Separation

## Status

Accepted

## Context

In initial versions of the AgentPay platform:
- The payment adapters named `RazorpayPaymentProvider` and `StripePaymentProvider` operated as dictionary-based in-memory simulations rather than making remote HTTP requests.
- The default application factory (`create_app()`) unconditionally instantiated `MockPaymentProvider()` regardless of configured environment variables (`.env.example`).
- This created ambiguity between simulated test fixtures and actual external gateway integrations (Remediation findings AP-004 and AP-005).
- Furthermore, `AGENTS.md` §151 strictly mandates that autonomous live-money payments default to disabled.

## Decision

We establish an unambiguous separation between simulated test adapters and real sandbox client implementations, orchestrated through an immutable application configuration object:

1. **Separation of Concerns**:
   - **Mock / Simulator Adapters**: `MockPaymentProvider` (and in-memory simulators) reside in `adapters/mock/` and are explicitly designated for hermetic, zero-dependency unit tests and offline development.
   - **Sandbox Client Adapters**: `RazorpaySandboxProvider` resides in `adapters/razorpay_sandbox_provider.py` and implements direct, authenticated HTTPS requests to the official provider sandbox endpoint (`https://api.razorpay.com/v1`). It uses strict connect (5.0s) and read (10.0s) timeouts, validates API responses, and never invents synthetic IDs.
2. **Immutable Runtime Configuration**:
   - Configuration is parsed and validated at startup via `AppConfig.from_env()`.
   - **Environment Modes**: Supported values are `development`, `test`, `sandbox`, and `production`.
   - **Provider Selection**: `AGENTPAY_PAYMENT_PROVIDER` accepts `mock`, `razorpay`, or `stripe`.
3. **Fail-Closed Validation**:
   - When `AGENTPAY_ENVIRONMENT=sandbox` is configured:
     - `AGENTPAY_PAYMENT_PROVIDER` must be explicitly set to a supported sandbox provider (e.g. `razorpay`).
     - Required credentials (`RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`) must be non-empty and must not contain obvious placeholder strings (`"your_key"`, `"placeholder"`).
     - Missing or invalid credentials cause immediate startup termination (`ConfigError`).
   - The platform never silently falls back from sandbox to mock mode upon credential errors.
4. **Authoritative Timeout Query & SAGA Compensation**:
   - If an outbound payment request times out or returns an uncertain status, the provider client queries the authoritative gateway status before making decisions.
   - Blind charge retries are strictly prohibited (§43).
   - On payment decline or failure, SAGA compensating transactions automatically release reserved inventory.
5. **Zero Live Money Invariant (§151)**:
   - Live-money payment capture remains strictly disabled in this release. All real gateway operations target provider sandbox environments.

## Consequences

- **Positive**: Eliminates developer confusion between mock tests and live gateway behavior.
- **Positive**: Prevents accidental sandbox or mock deployment into environments expecting hard financial gates.
- **Positive**: Satisfies Section 151 and remediation findings AP-004, AP-005, and AP-017.
- **Negative**: Sandbox integration tests require outbound network access to Razorpay sandbox endpoints (addressed by keeping unit test suites hermetic on mock adapters).

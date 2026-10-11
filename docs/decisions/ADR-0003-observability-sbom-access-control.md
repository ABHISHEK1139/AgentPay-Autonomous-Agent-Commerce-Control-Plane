# ADR-0003: Access Control and Gating for Observability Telemetry and Software Bill of Materials (SBOM)

## Status

Accepted

## Context

Blueprint §14 requires strict identity authentication and tenant scoping for all control plane interactions. Blueprint §47 requires comprehensive zero-dependency metrics and telemetry exposition (`GET /api/v1/metrics`). Blueprint §57 and §130 require continuous supply-chain provenance and CycloneDX 1.5 SBOM metadata (`GET /api/v1/security/sbom`).

During adversarial security audit (B-OBS-01), it was identified that unauthenticated exposition of system internals provides potential adversaries with actionable reconnaissance:
1. Operational metrics expose financial transaction counters, rate limiter pressures, error codes, and operational state.
2. CycloneDX SBOM metadata exposes exact pinned dependency versions and platform components, facilitating targeted CVE selection.

Simultaneously, standard Kubernetes/Prometheus deployment topologies often utilize network-isolated daemon scrapers that lack dynamic bearer token injection, while local development, demo scenarios, and developer test clients require low-friction telemetry inspection.

## Problem

How should the control plane balance standard operational telemetry scraping with defensive security against unauthenticated external reconnaissance?

## Options

1. **Always Unauthenticated**: Leave `/api/v1/metrics` and `/api/v1/security/sbom` open as public endpoints.
2. **Strictly Authenticated Without Config Seams**: Gate both endpoints behind mandatory `ADMIN` or `AGENT_OPERATOR` credentials in all environments.
3. **Configurable Defense-in-Depth with Fail-Closed Credential Validation**:
   - Provide explicit configuration `require_observability_auth` on `AgentPayApiApp` (configurable via `AGENTPAY_REQUIRE_OBSERVABILITY_AUTH` environment variable or factory parameter).
   - In hardened mode (`require_observability_auth=True`): require authenticated credentials bearing `AGENT_OPERATOR` or `ADMIN` roles. Unauthenticated requests return HTTP 401 `UNAUTHENTICATED`; unprivileged roles return HTTP 403 `UNAUTHORIZED_OBSERVABILITY_ACCESS`.
   - In standard mode (`require_observability_auth=False`): allow unauthenticated access for network-isolated telemetry scrapers and local development. Crucially, if authentication headers (`Authorization` or `X-API-Key`) *are* provided, validate them and fail closed (HTTP 401) on invalid/revoked/expired credentials.

## Decision

Option 3.

## Consequences

- Zero-trust production environments can engage strict telemetry access control by passing `require_observability_auth=True` or defining `AGENTPAY_REQUIRE_OBSERVABILITY_AUTH=true`.
- Local test suites, demo harnesses, and existing Prometheus scrapers remain functional without breaking backward compatibility.
- Tampered or revoked tokens submitted to telemetry endpoints fail closed immediately rather than silently succeeding.
- Eliminates information disclosure and attack-surface reconnaissance risks in production (§14, §47, §57, §130).

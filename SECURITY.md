# Security Policy

## Current Posture

AgentPay implements a defense-in-depth security model designed specifically for autonomous agent transactions:

- **AI Sits Below the Authorization Boundary (§2, §18, §134)**: LLMs and advisory engines (JEV) can never alter permissions, spending limits, policy rules, or credentials. Deterministic policy engines retain exclusive execution authority.
- **Fail-Closed Financial Guards (§2.4)**: Whenever uncertainty affects pricing, identity, balance, inventory, or policy, execution halts immediately and fails closed.
- **8-Field Bound Approvals (§20)**: Human approvals are cryptographically bound to exact action parameters: action ID, amount, currency, merchant ID, resource, action version, principal, and tenant. Single-use tokens prevent replay attacks.
- **Section 177 Adversarial Threat Harness**: 14 mandatory attack vectors (prompt injection, SSRF, replay, concurrency race, budget bypass, token forgery, parameter tampering, etc.) are verified across automated stress tests.
- **HMAC-SHA256 Webhook Signatures**: Raw request bytes are verified against provider secrets with a 300s replay window and mutex-guarded idempotency locks.
- **Zero Raw Payment Data (§40)**: AgentPay never handles or stores card numbers or CVV.

*Note: While tested against the Section 177 threat harness and audited via internal verification suites, AgentPay has not undergone an external third-party SOC 2 or PCI-DSS certification. See `docs/security/standards-matrix.md` and `docs/security/autonomous-audit-report.md` for detailed alignment mappings.*

## Reporting Vulnerabilities

We take security vulnerabilities seriously. Please report suspected vulnerabilities privately:

1. **Email**: Send detailed vulnerability disclosures to `security@agentpay.local` (or the repository maintainers directly).
2. **Details**: Include affected components, complete reproduction steps, proof-of-concept payloads, and potential impact.
3. **Coordinated Disclosure**: Do not open public GitHub issues or discussions for unpatched security vulnerabilities. We will acknowledge receipt within 48 hours and coordinate remediation.

## Non-Negotiable Invariants for Contributors

- **Zero Hardcoded Secrets**: Never commit API keys, private certificates, webhook secrets, or customer data. Always use `.env.example` to document environment variable names (§58).
- **No Weakened Assertions**: Never disable authentication, bypass tenant checks, or weaken assertions to make tests pass (§78, §135).
- **Tenant Scope Isolation**: Every data access method must accept and enforce explicit `tenant_id` boundaries (§49–§51).
- **Idempotent Financial Operations**: Payments, refunds, and order state mutations must be idempotent and guarded by payload conflict detection (§42).

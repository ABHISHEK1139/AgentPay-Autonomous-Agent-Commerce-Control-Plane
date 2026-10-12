# Agent Protocol Adapters Specification (§107, §108)

> **Document Status**: Prototype / Local Translation Reference (Engineering Preview)  
> **Governing Specifications**: `AGENTS.md` (§10 Layering, §107 Protocol Overview, §108 Adapter Design Rules)  
> **Protocols Supported**: ACP (Agentic Commerce Protocol — Draft Translation), AP2 (Agent Payments Protocol — Specification Alignment), UAP (Universal Agent Protocol — AgentPay Internal Prototype), x402 (HTTP 402 Extension — Prototype Translation)  
> **Last Updated**: Remediation Sprint 10 (2026-10-12)  
> **Interoperability Notice**: These adapters provide internal translation to canonical `ActionRequest` domain objects. They are verified against local schema suites and adversarial stress probes; external RFC or formal third-party certification has not been performed.

---

## 1. Architectural Philosophy & Isolation Invariants (§10, §108)

External AI agent protocols are evolving across the agentic commerce ecosystem. AgentPay bridges external protocol payloads without compromising core domain integrity:

1. **Zero Domain Pollution (§10)**:  
   Protocol-specific message structures, headers, and serialization formats reside strictly inside `apps/api/src/agentpay/adapters/protocols/`. They are NEVER imported into `agentpay.domain`.
2. **Canonical Translation to `ActionRequest` (§6)**:  
   Every external message is validated, sanitized, and translated into the canonical internal `ActionRequest` object before entering the application orchestrator.
3. **Strict Validation & Fail-Closed**:  
   Malformed payloads, unsupported protocol versions, and missing signature proofs immediately fail closed with typed protocol errors (`MalformedPayloadError`, `UnsupportedProtocolVersionError`).
4. **Advisory AI Boundaries (§2, §18, §134)**:  
   No external protocol payload or AI agent instruction may bypass deterministic policy limits or widen authorization boundaries.

---

## 2. Protocol Master Specification & Register

### 2.1 Agentic Commerce Protocol (ACP)
- **Status**: Local Prototype Translation
- **Official Specification**: [OpenAI Agentic Commerce Protocol](https://developers.openai.com/commerce) / [Checkout Specification](https://developers.openai.com/commerce/specs/checkout)
- **Adapter Module**: `apps/api/src/agentpay/adapters/protocols/acp.py` (`AcpAdapter`)
- **Route**: `POST /api/v1/protocols/acp/checkout`
- **Message Format**: JSON payload with `protocol_version: "acp-1.0"`, `cart: {items: [...]}`, `buyer: {tenant_id, principal_id, agent_id}`.
- **Translation**: Maps cart item variant, pricing, and currency directly to canonical `ActionRequest`.
- **Supported Flows**: Cart checkout, single-item quote, and immediate execution request.
- **Unsupported / Scope Limit**: Multi-party payment escrow, recurring billing mandates.

### 2.2 Agent Payments Protocol (AP2)
- **Status**: Local Prototype Translation
- **Official Specification**: [Google Agent Payments Protocol (AP2)](https://ap2-protocol.org/) / [AP2 GitHub](https://github.com/google-agentic-commerce/AP2)
- **Adapter Module**: `apps/api/src/agentpay/adapters/protocols/ap2.py` (`Ap2Adapter`)
- **Route**: `POST /api/v1/protocols/ap2/mandates`
- **Message Format**: Mandate-based authorization structure with cryptographic delegation proof.
- **Translation**: Extracts intent limits, checks mandate expiration, and maps to `ActionRequest` with `AuthorizationMode.DELEGATED`.
- **Supported Flows**: Cryptographically delegated mandates, bounded spending authorizations.
- **Unsupported / Scope Limit**: On-chain smart contract settlement proofs.

### 2.3 Universal Agent Protocol (UAP)
- **Status**: AgentPay Internal Prototype Protocol (Not an external industry standard)
- **Specification**: Internal AgentPay RPC Specification
- **Adapter Module**: `apps/api/src/agentpay/adapters/protocols/uap.py` (`UapAdapter`)
- **Route**: `POST /api/v1/protocols/uap/rpc`
- **Message Format**: JSON-RPC 2.0 format (`method: "commerce.purchase"`).
- **Translation**: Converts RPC parameters into `ActionRequest` and maps domain execution results back to standard JSON-RPC `result` or `error` envelopes.
- **Supported Flows**: Remote procedure call execution for registered agents.
- **Scope Limit**: Designed specifically for internal and partner service-to-service communication.

### 2.4 x402 Protocol (HTTP 402 Extension)
- **Status**: Local Prototype Translation
- **Official Specification / Starting Point**: [Coinbase Developer Platform x402](https://www.coinbase.com/en-in/developer-platform/discover/launches/x402)
- **Adapter Module**: `apps/api/src/agentpay/adapters/protocols/x402.py` (`X402Adapter`)
- **Route**: `POST /api/v1/protocols/x402/pay`
- **Message Format**: Pay-per-call HTTP 402 challenge header response.
- **Translation**: Validates challenge token and amount, mapping micropayment to canonical `ActionRequest`.
- **Supported Flows**: HTTP 402 challenge-response micropayments.
- **Scope Limit**: Single-call tokenized micropayment challenge settlement.

---

## 3. Test & Verification Matrix

| Protocol | Adapter Module | Unit Test File | Adversarial Stress Suite |
|---|---|---|---|
| **ACP** | `acp.py` | `apps/api/tests/unit/test_protocol_adapters.py` | `tests/adversarial/stress_test_protocols.py` |
| **AP2** | `ap2.py` | `apps/api/tests/unit/test_protocol_adapters.py` | `tests/adversarial/stress_test_protocols.py` |
| **UAP** | `uap.py` | `apps/api/tests/unit/test_protocol_adapters.py` | `tests/adversarial/stress_test_protocols.py` |
| **x402** | `x402.py` | `apps/api/tests/unit/test_protocol_adapters.py` | `tests/adversarial/stress_test_protocols.py` |

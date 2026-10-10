# Agent Protocol Adapters Specification (§107, §108)

> **Document Status**: Production Standard  
> **Governing Specifications**: `AGENTS.md` (§10 Layering, §107 Protocol Overview, §108 Adapter Design Rules)  
> **Protocols Supported**: ACP (Agent Commerce Protocol), AP2 (Agent Payment Protocol), UAP (Universal Agent Protocol), x402 (HTTP 402 Extension)  
> **Last Updated**: Sprint 13 Hardening (2026-10-10)  

---

## 1. Architectural Philosophy & Isolation Invariants (§10, §108)

External AI agent protocols are evolving rapidly across the industry. AgentPay bridges multiple external protocols without compromising core system integrity:

1. **Zero Domain Pollution (§10)**:  
   Protocol-specific message structures, headers, and serialization formats reside strictly inside `adapters/protocols/`. They must NEVER be imported into `domain/`.
2. **Canonical Translation to `ActionRequest` (§6)**:  
   Every external message is validated, sanitized, and translated into the canonical internal `ActionRequest` object before entering the application orchestrator.
3. **Strict Validation & Failure Closed**:  
   Malformed payloads, unsupported protocol versions, and missing signature proofs immediately fail closed with typed protocol errors (`MalformedPayloadError`, `UnsupportedProtocolVersionError`).

---

## 2. Protocol Master Specification

### 2.1 Agent Commerce Protocol (ACP)
- **Adapter**: `AcpAdapter` in `apps/api/src/agentpay/adapters/protocols/acp_adapter.py`
- **Route**: `POST /api/v1/protocols/acp/checkout`
- **Message Format**: JSON payload with `protocol_version: "acp-1.0"`, `cart: {items: [...]}`, `buyer: {tenant_id, principal_id, agent_id}`.
- **Translation**: Maps cart item variant, pricing, and currency directly to canonical `ActionRequest`.

### 2.2 Agent Payment Protocol (AP2)
- **Adapter**: `Ap2Adapter` in `apps/api/src/agentpay/adapters/protocols/ap2_adapter.py`
- **Route**: `POST /api/v1/protocols/ap2/mandates`
- **Message Format**: Mandate-based authorization structure with cryptographic delegation proof.
- **Translation**: Extracts intent limits, checks mandate expiration, and maps to `ActionRequest` with `AuthorizationMode.DELEGATED`.

### 2.3 Universal Agent Protocol (UAP)
- **Adapter**: `UapAdapter` in `apps/api/src/agentpay/adapters/protocols/uap_adapter.py`
- **Route**: `POST /api/v1/protocols/uap/rpc`
- **Message Format**: JSON-RPC 2.0 format (`method: "commerce.purchase"`).
- **Translation**: Converts RPC parameters into `ActionRequest` and maps domain execution results back to standard JSON-RPC `result` or `error` envelopes.

### 2.4 x402 Protocol (HTTP 402 Micropayments)
- **Adapter**: `X402Adapter` in `apps/api/src/agentpay/adapters/protocols/x402_adapter.py`
- **Route**: `POST /api/v1/protocols/x402/pay`
- **Message Format**: Pay-per-call HTTP 402 challenge header response.
- **Translation**: Validates challenge token and amount, mapping micropayment to canonical `ActionRequest`.

---

## 3. Test & Compliance Matrix

| Protocol | Adapter Module | Unit Test File | Adversarial Challenge File |
|---|---|---|---|
| **ACP** | `acp_adapter.py` | `test_protocol_adapters.py` | `test_protocol_adversarial_challenger.py` |
| **AP2** | `ap2_adapter.py` | `test_protocol_adapters.py` | `test_protocol_adversarial_challenger.py` |
| **UAP** | `uap_adapter.py` | `test_protocol_adapters.py` | `test_protocol_adversarial_challenger.py` |
| **x402** | `x402_adapter.py` | `test_protocol_adapters.py` | `test_protocol_adversarial_challenger.py` |

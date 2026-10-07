"""Agent Protocol Adapter Layer (§107, §108).

Provides adapter prototypes translating external agent commerce and payment
protocols into AgentPay canonical ActionRequests and vice versa:
- ACP (Agent Commerce Protocol): e-commerce checkout and cart orchestration.
- AP2 (Agent Payment Protocol): payment mandates and invoice settlement.
- UAP (Universal Agent Protocol): JSON-RPC capability invocations.
- x402 (HTTP 402 Payment Required): paywalled resource challenges and settlement tokens.
"""

from __future__ import annotations

from agentpay.adapters.protocols.acp import (
    ACPAdapter,
    AcpAdapter,
    ACPBuyer,
    AcpBuyer,
    AcpCartItem,
    ACPCheckoutRequest,
    ACPItem,
    AcpPurchaseRequest,
    AcpPurchaseResponse,
    ACPResponse,
)
from agentpay.adapters.protocols.ap2 import (
    AP2Adapter,
    Ap2Adapter,
    AP2Payee,
    Ap2Payee,
    AP2Payer,
    Ap2Payer,
    AP2PaymentMandateRequest,
    Ap2PaymentRequest,
    Ap2PaymentResponse,
    AP2Response,
)
from agentpay.adapters.protocols.base import (
    BaseProtocolAdapter,
    MalformedPayloadError,
    ProtocolError,
    ProtocolTranslationError,
    UnsupportedActionError,
    UnsupportedProtocolVersionError,
    format_error_envelope,
    parse_iso_utc,
)
from agentpay.adapters.protocols.uap import (
    UAPAdapter,
    UapAdapter,
    UAPError,
    UapError,
    UAPHeader,
    UapHeader,
    UapMessage,
    UAPRequest,
    UAPResponse,
    UapResponse,
)
from agentpay.adapters.protocols.x402 import (
    X402Adapter,
    X402Challenge,
    X402ChallengeRequest,
    X402PaymentRequest,
    X402PaymentResponse,
    X402SettlementProof,
)

__all__ = [
    "ACPAdapter",
    "ACPBuyer",
    "ACPCheckoutRequest",
    "ACPItem",
    "ACPResponse",
    "AP2Adapter",
    "AP2Payee",
    "AP2Payer",
    "AP2PaymentMandateRequest",
    "AP2Response",
    # ACP
    "AcpAdapter",
    "AcpBuyer",
    "AcpCartItem",
    "AcpPurchaseRequest",
    "AcpPurchaseResponse",
    # AP2
    "Ap2Adapter",
    "Ap2Payee",
    "Ap2Payer",
    "Ap2PaymentRequest",
    "Ap2PaymentResponse",
    # Base and Errors
    "BaseProtocolAdapter",
    "MalformedPayloadError",
    "ProtocolError",
    "ProtocolTranslationError",
    "UAPAdapter",
    "UAPError",
    "UAPHeader",
    "UAPRequest",
    "UAPResponse",
    # UAP
    "UapAdapter",
    "UapError",
    "UapHeader",
    "UapMessage",
    "UapResponse",
    "UnsupportedActionError",
    "UnsupportedProtocolVersionError",
    # x402
    "X402Adapter",
    "X402Challenge",
    "X402ChallengeRequest",
    "X402PaymentRequest",
    "X402PaymentResponse",
    "X402SettlementProof",
    "format_error_envelope",
    "parse_iso_utc",
]

"""AgentPay Observability Subsystem (§47)."""

from __future__ import annotations

from agentpay.infrastructure.observability.meter import Counter, Histogram, Meter
from agentpay.infrastructure.observability.tracer import Span, Tracer

_DEFAULT_TRACER = Tracer("agentpay")
_DEFAULT_METER = Meter("agentpay")


def get_tracer(name: str = "agentpay") -> Tracer:
    """Returns the global default tracer instance."""
    if name == "agentpay":
        return _DEFAULT_TRACER
    return Tracer(name)


def get_meter(name: str = "agentpay") -> Meter:
    """Returns the global default meter instance."""
    if name == "agentpay":
        return _DEFAULT_METER
    return Meter(name)


__all__ = [
    "Counter",
    "Histogram",
    "Meter",
    "Span",
    "Tracer",
    "get_meter",
    "get_tracer",
]

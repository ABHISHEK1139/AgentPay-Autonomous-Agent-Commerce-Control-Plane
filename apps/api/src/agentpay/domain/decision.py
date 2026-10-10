"""Pure domain models for Decision Intelligence / JEV advisory risk assessment (§28, §163).

Following Clean Architecture (§8, §10):
- Pure Python standard library and domain Money/errors only.
- Advisory intelligence sits BELOW the authorization boundary (§2, §18, §134).
- Model confidence cannot override deterministic policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agentpay.domain.errors import InvalidDecisionError
from agentpay.domain.money import Money


class RiskLevel(StrEnum):
    """Advisory risk categorization produced by decision models (§156, §157)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecisionRecommendation(StrEnum):
    """Advisory recommendation from decision models (§28).

    Note: These are advisory suggestions. The deterministic policy engine
    remains the sole authoritative gatekeeper (§2.2).
    """

    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class DecisionFactor:
    """An explainable factor contributing to a decision outcome (§29, §119)."""

    code: str
    description: str
    weight: float

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("code", self.code),
            ("description", self.description),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidDecisionError(f"{name} must be a non-empty string")

        weight_obj: object = self.weight
        if isinstance(weight_obj, bool) or not isinstance(weight_obj, (int, float)):
            raise InvalidDecisionError(f"weight must be a float, got {weight_obj!r}")


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Contextual input provided to decision intelligence models (§28)."""

    action_id: str
    tenant_id: str
    agent_id: str
    amount: Money | None = None
    merchant_id: str | None = None
    category: str | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("action_id", self.action_id),
            ("tenant_id", self.tenant_id),
            ("agent_id", self.agent_id),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidDecisionError(f"{name} must be a non-empty string")

        if self.amount is not None and not isinstance(self.amount, Money):
            raise InvalidDecisionError("amount must be None or a Money instance")


@dataclass(frozen=True, slots=True)
class Decision:
    """An immutable decision evaluation output (§28, §29)."""

    decision_id: str
    recommendation: DecisionRecommendation
    risk_level: RiskLevel
    confidence: float
    factors: tuple[DecisionFactor, ...] = field(default_factory=tuple)
    model_provider: str = "mock"
    model_version: str = "v1"
    prompt_version: str | None = None
    tool_policy_version: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("decision_id", self.decision_id),
            ("model_provider", self.model_provider),
            ("model_version", self.model_version),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidDecisionError(f"{name} must be a non-empty string")

        conf_obj: object = self.confidence
        if (
            isinstance(conf_obj, bool)
            or not isinstance(conf_obj, (int, float))
            or not (0.0 <= conf_obj <= 1.0)
        ):
            raise InvalidDecisionError(
                f"confidence must be a float between 0.0 and 1.0, got {conf_obj!r}"
            )

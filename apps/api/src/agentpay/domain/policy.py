from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from agentpay.domain.errors import InvalidPolicyInputError
from agentpay.domain.money import Money


class PolicyDecision(StrEnum):
    """Security-critical outcomes stay structured; a bare boolean is never acceptable."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REVIEW = "REVIEW"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"


_DECISION_SEVERITY: Final[dict[PolicyDecision, int]] = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.REVIEW: 1,
    PolicyDecision.NEEDS_INFORMATION: 2,
    PolicyDecision.DENY: 3,
}


def decision_severity(decision: PolicyDecision) -> int:
    return _DECISION_SEVERITY[decision]


@dataclass(frozen=True, slots=True)
class PolicyReason:
    code: str
    message: str

    def __post_init__(self) -> None:
        parts: tuple[tuple[str, object], ...] = (("code", self.code), ("message", self.message))
        for field_name, value in parts:
            if not isinstance(value, str) or not value.strip():
                raise InvalidPolicyInputError(f"reason {field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class PolicyEvaluationResult:
    decision: PolicyDecision
    policy_version: str
    reasons: tuple[PolicyReason, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        policy_version: object = self.policy_version
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise InvalidPolicyInputError("policy_version must be a non-empty string")

    @classmethod
    def allow(cls, policy_version: str) -> PolicyEvaluationResult:
        return cls(PolicyDecision.ALLOW, policy_version)

    @classmethod
    def deny(cls, policy_version: str, reasons: tuple[PolicyReason, ...]) -> PolicyEvaluationResult:
        return cls(PolicyDecision.DENY, policy_version, reasons)

    @classmethod
    def review(
        cls, policy_version: str, reasons: tuple[PolicyReason, ...]
    ) -> PolicyEvaluationResult:
        return cls(PolicyDecision.REVIEW, policy_version, reasons)

    @property
    def requires_approval(self) -> bool:
        return self.decision is PolicyDecision.REVIEW

    @property
    def is_permitted(self) -> bool:
        return self.decision is not PolicyDecision.DENY

    def most_restrictive(self, other: PolicyEvaluationResult) -> PolicyEvaluationResult:
        """Combine two evaluations of the same policy version conservatively."""
        if self.policy_version != other.policy_version:
            raise InvalidPolicyInputError(
                f"cannot combine policy versions {self.policy_version} and {other.policy_version}"
            )
        if decision_severity(self.decision) >= decision_severity(other.decision):
            winner, loser = self, other
        else:
            winner, loser = other, self
        return PolicyEvaluationResult(
            winner.decision, self.policy_version, winner.reasons + loser.reasons
        )


@dataclass(frozen=True, slots=True)
class SpendingLimits:
    """Hard ceilings and the approval threshold. ``None`` means the limit is not configured."""

    per_transaction_minor: int | None = None
    daily_minor: int | None = None
    requires_approval_above_minor: int | None = None

    def __post_init__(self) -> None:
        ceilings: tuple[tuple[str, object], ...] = (
            ("per_transaction_minor", self.per_transaction_minor),
            ("daily_minor", self.daily_minor),
            ("requires_approval_above_minor", self.requires_approval_above_minor),
        )
        for field_name, value in ceilings:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidPolicyInputError(
                    f"{field_name} must be None or a non-negative int, got {value!r}"
                )


@dataclass(frozen=True, slots=True)
class SpendingPolicyRequest:
    amount: Money
    spent_today_minor: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Money):
            raise InvalidPolicyInputError("amount must be a Money")
        spent_today: object = self.spent_today_minor
        if isinstance(spent_today, bool) or not isinstance(spent_today, int) or spent_today < 0:
            raise InvalidPolicyInputError(
                f"spent_today_minor must be a non-negative int, got {spent_today!r}"
            )


class SpendingPolicyEngine:
    """Deterministic spending policy. No model output, no clock, no I/O."""

    def evaluate(
        self,
        request: SpendingPolicyRequest,
        limits: SpendingLimits,
        policy_version: str,
    ) -> PolicyEvaluationResult:
        reasons: list[PolicyReason] = []
        decision = PolicyDecision.ALLOW

        per_transaction = limits.per_transaction_minor
        if per_transaction is not None and request.amount.minor_units > per_transaction:
            decision = PolicyDecision.DENY
            reasons.append(
                PolicyReason(
                    code="PER_TRANSACTION_LIMIT_EXCEEDED",
                    message=(
                        f"Requested {request.amount.minor_units} {request.amount.currency} exceeds "
                        f"the per-transaction maximum of {per_transaction}."
                    ),
                )
            )

        daily = limits.daily_minor
        if daily is not None and request.spent_today_minor + request.amount.minor_units > daily:
            decision = PolicyDecision.DENY
            reasons.append(
                PolicyReason(
                    code="DAILY_LIMIT_EXCEEDED",
                    message=(
                        f"Requested {request.amount.minor_units} on top of "
                        f"{request.spent_today_minor} already spent today exceeds the daily "
                        f"maximum of {daily}."
                    ),
                )
            )

        approval_threshold = limits.requires_approval_above_minor
        if approval_threshold is not None and request.amount.minor_units > approval_threshold:
            if decision_severity(PolicyDecision.REVIEW) > decision_severity(decision):
                decision = PolicyDecision.REVIEW
            reasons.append(
                PolicyReason(
                    code="APPROVAL_REQUIRED",
                    message=(
                        f"Requested {request.amount.minor_units} exceeds the autonomous limit of "
                        f"{approval_threshold}; human approval is required."
                    ),
                )
            )

        if decision is PolicyDecision.ALLOW:
            return PolicyEvaluationResult.allow(policy_version)
        return PolicyEvaluationResult(decision, policy_version, tuple(reasons))

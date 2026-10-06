from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentpay.domain.errors import InvalidPolicyInputError
from agentpay.domain.money import Money
from agentpay.domain.policy import (
    PolicyDecision,
    PolicyEvaluationResult,
    PolicyReason,
    SpendingLimits,
    SpendingPolicyEngine,
    SpendingPolicyRequest,
    decision_severity,
)

POLICY_VERSION = "policy-17"
CURRENCY = "INR"


def evaluate(
    amount_minor: int,
    *,
    per_transaction: int | None = 2000,
    daily: int | None = 5000,
    approval_above: int | None = 2000,
    spent_today: int = 0,
    policy_version: str = POLICY_VERSION,
) -> PolicyEvaluationResult:
    return SpendingPolicyEngine().evaluate(
        SpendingPolicyRequest(amount=Money(amount_minor, CURRENCY), spent_today_minor=spent_today),
        SpendingLimits(
            per_transaction_minor=per_transaction,
            daily_minor=daily,
            requires_approval_above_minor=approval_above,
        ),
        policy_version,
    )


def reason_codes(result: PolicyEvaluationResult) -> set[str]:
    return {reason.code for reason in result.reasons}


def test_amount_within_autonomous_limit_is_allowed() -> None:
    result = evaluate(1299)
    assert result.decision is PolicyDecision.ALLOW
    assert result.reasons == ()
    assert result.is_permitted
    assert not result.requires_approval


def test_amount_above_autonomous_limit_requires_approval() -> None:
    result = evaluate(8000, per_transaction=20000, daily=100000, approval_above=2000)
    assert result.decision is PolicyDecision.REVIEW
    assert result.requires_approval
    assert reason_codes(result) == {"APPROVAL_REQUIRED"}


def test_amount_above_hard_ceiling_is_denied_not_merely_reviewed() -> None:
    result = evaluate(50000, per_transaction=20000, daily=100000, approval_above=2000)
    assert result.decision is PolicyDecision.DENY
    assert not result.is_permitted
    assert "PER_TRANSACTION_LIMIT_EXCEEDED" in reason_codes(result)
    assert "APPROVAL_REQUIRED" in reason_codes(result)


def test_daily_limit_counts_spend_already_made_today() -> None:
    result = evaluate(
        3000, per_transaction=10000, daily=5000, approval_above=None, spent_today=2500
    )
    assert result.decision is PolicyDecision.DENY
    assert "DAILY_LIMIT_EXCEEDED" in reason_codes(result)


def test_unconfigured_limits_do_not_constrain() -> None:
    result = evaluate(10**9, per_transaction=None, daily=None, approval_above=None)
    assert result.decision is PolicyDecision.ALLOW


def test_boundary_amount_at_threshold_is_not_above_it() -> None:
    assert evaluate(2000).decision is PolicyDecision.ALLOW


def test_deny_reason_message_never_leaks_another_tenants_data() -> None:
    result = evaluate(50000, per_transaction=20000, daily=None, approval_above=None)
    assert all("tenant" not in reason.message.lower() for reason in result.reasons)


def test_evaluation_is_deterministic_across_repeated_calls() -> None:
    first = evaluate(8000, per_transaction=20000, daily=100000, approval_above=2000)
    second = evaluate(8000, per_transaction=20000, daily=100000, approval_above=2000)
    assert first == second


def test_policy_version_is_required() -> None:
    with pytest.raises(InvalidPolicyInputError):
        evaluate(100, policy_version="")


def test_negative_spend_history_is_rejected() -> None:
    with pytest.raises(InvalidPolicyInputError):
        SpendingPolicyRequest(amount=Money(100, CURRENCY), spent_today_minor=-1)


def test_negative_limits_are_rejected() -> None:
    with pytest.raises(InvalidPolicyInputError):
        SpendingLimits(per_transaction_minor=-1)


def test_most_restrictive_keeps_the_stricter_decision() -> None:
    reason = PolicyReason(code="X", message="x")
    allow = PolicyEvaluationResult.allow(POLICY_VERSION)
    deny = PolicyEvaluationResult.deny(POLICY_VERSION, (reason,))
    assert allow.most_restrictive(deny).decision is PolicyDecision.DENY
    assert deny.most_restrictive(allow).decision is PolicyDecision.DENY


def test_most_restrictive_refuses_to_mix_policy_versions() -> None:
    allow = PolicyEvaluationResult.allow(POLICY_VERSION)
    other = PolicyEvaluationResult.allow("policy-18")
    with pytest.raises(InvalidPolicyInputError):
        allow.most_restrictive(other)


def test_decision_severity_ordering_is_total() -> None:
    severities = {decision_severity(d) for d in PolicyDecision}
    assert severities == {0, 1, 2, 3}


NON_NEGATIVE_MINOR = st.integers(min_value=0, max_value=10**12)


@given(amount=NON_NEGATIVE_MINOR, spent_today=NON_NEGATIVE_MINOR)
def test_increasing_amount_can_never_reduce_required_strictness(
    amount: int, spent_today: int
) -> None:
    if amount == 0:
        return
    limits = SpendingLimits(
        per_transaction_minor=200_000,
        daily_minor=500_000,
        requires_approval_above_minor=20_000,
    )
    engine = SpendingPolicyEngine()

    def severity_for(value: int) -> int:
        result = engine.evaluate(
            SpendingPolicyRequest(amount=Money(value, CURRENCY), spent_today_minor=spent_today),
            limits,
            POLICY_VERSION,
        )
        return decision_severity(result.decision)

    assert severity_for(amount) >= severity_for(amount - 1)


@given(amount=NON_NEGATIVE_MINOR, per_transaction=NON_NEGATIVE_MINOR, daily=NON_NEGATIVE_MINOR)
def test_amount_never_exceeds_configured_ceiling_when_allowed(
    amount: int, per_transaction: int, daily: int
) -> None:
    result = evaluate(amount, per_transaction=per_transaction, daily=daily, approval_above=None)
    if result.decision is not PolicyDecision.DENY:
        assert amount <= per_transaction
        assert amount <= daily


@given(amount=NON_NEGATIVE_MINOR)
def test_any_denial_always_carries_an_explanatory_reason(amount: int) -> None:
    result = evaluate(amount)
    if result.decision is PolicyDecision.DENY:
        assert result.reasons, "a DENY without a reason is unauditable"
        assert all(isinstance(reason, PolicyReason) for reason in result.reasons)

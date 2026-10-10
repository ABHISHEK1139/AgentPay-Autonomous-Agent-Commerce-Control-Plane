"""Unit tests for advisory decision model version provenance and metadata validation (§29, §118).

Verifies:
- String validation for prompt_version, tool_policy_version, and policy_version in Decision.__post_init__.
- Rejection of empty or whitespace-only version strings with InvalidDecisionError.
- Preservation of backward compatibility when version parameters are None.
- MockDecisionEngine populates standardized model, prompt, tool policy, and spending policy versions.
- PurchaseOrchestrator emits AuditEventType.JEV_EVALUATED with complete configuration provenance.
- Strict absence of private internal chain-of-thought or reasoning text in audit records (§29).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.mock.mock_decision_engine import MockDecisionEngine
from agentpay.domain.action_request import (
    ActionRequest,
    Intent,
    ResourceRef,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.decision import (
    Decision,
    DecisionContext,
    DecisionRecommendation,
    RiskLevel,
)
from agentpay.domain.errors import InvalidDecisionError
from agentpay.domain.money import Money


def test_decision_version_validation_success() -> None:
    """Valid non-empty version strings are accepted during Decision creation."""
    dec = Decision(
        decision_id="dec_valid_01",
        recommendation=DecisionRecommendation.ALLOW,
        risk_level=RiskLevel.LOW,
        confidence=0.95,
        model_provider="mock",
        model_version="mock-v1",
        prompt_version="prompt-commerce-v1",
        tool_policy_version="tool-policy-v1",
        policy_version="spending-policy-v1",
    )
    assert dec.prompt_version == "prompt-commerce-v1"
    assert dec.tool_policy_version == "tool-policy-v1"
    assert dec.policy_version == "spending-policy-v1"


def test_decision_version_defaults_preserve_backward_compatibility() -> None:
    """Omitting version parameters defaults to None without validation errors."""
    dec = Decision(
        decision_id="dec_compat_01",
        recommendation=DecisionRecommendation.ALLOW,
        risk_level=RiskLevel.LOW,
        confidence=0.90,
    )
    assert dec.prompt_version is None
    assert dec.tool_policy_version is None
    assert dec.policy_version is None


@pytest.mark.parametrize(
    "invalid_version",
    [
        "",
        "   ",
        "\t\n",
    ],
)
def test_decision_empty_prompt_version_raises_invalid_decision_error(invalid_version: str) -> None:
    """Empty or whitespace-only prompt_version fails closed with InvalidDecisionError."""
    with pytest.raises(
        InvalidDecisionError, match="prompt_version must be None or a non-empty string"
    ):
        Decision(
            decision_id="dec_inv_01",
            recommendation=DecisionRecommendation.ALLOW,
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            prompt_version=invalid_version,
        )


@pytest.mark.parametrize(
    "invalid_version",
    [
        "",
        "   ",
        "\t\n",
    ],
)
def test_decision_empty_tool_policy_version_raises_invalid_decision_error(
    invalid_version: str,
) -> None:
    """Empty or whitespace-only tool_policy_version fails closed with InvalidDecisionError."""
    with pytest.raises(
        InvalidDecisionError, match="tool_policy_version must be None or a non-empty string"
    ):
        Decision(
            decision_id="dec_inv_02",
            recommendation=DecisionRecommendation.ALLOW,
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            tool_policy_version=invalid_version,
        )


@pytest.mark.parametrize(
    "invalid_version",
    [
        "",
        "   ",
        "\t\n",
    ],
)
def test_decision_empty_policy_version_raises_invalid_decision_error(invalid_version: str) -> None:
    """Empty or whitespace-only policy_version fails closed with InvalidDecisionError."""
    with pytest.raises(
        InvalidDecisionError, match="policy_version must be None or a non-empty string"
    ):
        Decision(
            decision_id="dec_inv_03",
            recommendation=DecisionRecommendation.ALLOW,
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            policy_version=invalid_version,
        )


def test_mock_decision_engine_populates_version_provenance() -> None:
    """MockDecisionEngine produces complete version provenance across evaluation branches."""
    engine = MockDecisionEngine()
    context = DecisionContext(
        action_id="act_mock_dec",
        tenant_id="tenant_01",
        agent_id="agent_01",
        amount=Money(100000, "INR"),
        category="commerce",
    )
    decision = engine.evaluate(context)

    assert decision.model_provider == "mock"
    assert decision.model_version == "mock-v1"
    assert decision.prompt_version == "prompt-commerce-v1"
    assert decision.tool_policy_version == "tool-policy-v1"
    assert decision.policy_version == "spending-policy-v1"


def test_jev_evaluated_audit_event_records_metadata_provenance_without_chain_of_thought() -> None:
    """PurchaseOrchestrator emits JEV_EVALUATED audit event with versions and NO chain-of-thought text (§29)."""
    orch, _, _, _, _, _, _, _, _, _, _ = setup_orchestrator()

    action = ActionRequest(
        action_id="act_jev_prov_01",
        tenant_id="tenant_01",
        principal_id="user_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_01"),
        requested_at=datetime.now(UTC),
        intent=Intent(purpose="laptop procurement"),
        amount=Money(minor_units=129900, currency="INR"),
    )

    res = orch.execute_purchase(
        action=action,
        tenant_id="tenant_01",
        variant_id="var_01",
        trace_id="trc_jev_test_01",
    )
    assert res.decision_summary is not None

    events = orch._audit_ledger.get_events("tenant_01", action_id="act_jev_prov_01")  # type: ignore[union-attr]
    jev_events = [e for e in events if e.event_type == AuditEventType.JEV_EVALUATED]

    assert len(jev_events) == 1
    jev_event = jev_events[0]
    assert jev_event.trace_id == "trc_jev_test_01"

    data = jev_event.data
    # 1. Assert configuration provenance fields are present
    assert data.get("model_version") == "mock-v1"
    assert data.get("prompt_version") == "prompt-commerce-v1"
    assert data.get("tool_policy_version") == "tool-policy-v1"
    assert data.get("policy_version") == "spending-policy-v1"
    assert data.get("recommendation") == "ALLOW"
    assert data.get("risk_level") == "LOW"

    # 2. Assert STRICT absence of hidden chain-of-thought or reasoning text per §29
    prohibited_keys = {
        "chain_of_thought",
        "thought_process",
        "reasoning",
        "internal_monologue",
        "scratchpad",
        "prompt_text",
        "system_prompt",
    }
    assert not prohibited_keys.intersection(data.keys()), (
        f"Security Violation: Audit record contains prohibited reasoning keys: {prohibited_keys.intersection(data.keys())}"
    )

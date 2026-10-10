from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentpay.adapters.model_gateway.model_gateway import ModelGateway
from agentpay.domain.decision import (
    Decision,
    DecisionContext,
    DecisionRecommendation,
    RiskLevel,
)
from agentpay.domain.money import Money

DATASET_PATH = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "evaluation" / "dataset_v1.json"
)


def load_dataset() -> dict[str, Any]:
    assert DATASET_PATH.is_file(), f"Evaluation benchmark dataset not found at {DATASET_PATH}"
    content = DATASET_PATH.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(content)
    return data


def test_evaluation_benchmark_dataset_integrity() -> None:
    """Verifies that the versioned benchmark dataset conforms to required schema (§117)."""
    dataset = load_dataset()
    assert dataset["version"] == "1.0.0"
    assert dataset["dataset_name"] == "agentpay_model_evaluation_benchmark"
    assert isinstance(dataset["scenarios"], list)
    assert len(dataset["scenarios"]) >= 10

    for scenario in dataset["scenarios"]:
        assert "id" in scenario
        assert "category" in scenario
        assert "description" in scenario
        assert "input" in scenario
        assert "expected" in scenario

        expected = scenario["expected"]
        assert expected["recommendation"] in [r.value for r in DecisionRecommendation]
        assert expected["risk_level"] in [lvl.value for lvl in RiskLevel]


def get_benchmark_scenarios() -> list[dict[str, Any]]:
    dataset = load_dataset()
    scenarios: list[dict[str, Any]] = dataset["scenarios"]
    return scenarios


@pytest.mark.parametrize(
    "scenario",
    get_benchmark_scenarios(),
    ids=[s["id"] for s in get_benchmark_scenarios()],
)
def test_model_gateway_evaluates_benchmark_scenario(scenario: dict[str, Any]) -> None:
    """Evaluates each benchmark scenario and verifies 100% adherence to expected risk criteria (§117)."""
    gateway = ModelGateway()
    inp = scenario["input"]
    exp = scenario["expected"]

    amount = Money(inp["amount_minor"], inp["currency"]) if "amount_minor" in inp else None

    context = DecisionContext(
        action_id=inp["action_id"],
        tenant_id=inp["tenant_id"],
        agent_id=inp["agent_id"],
        amount=amount,
        category=inp.get("category"),
    )

    decision = gateway.evaluate(context)
    assert isinstance(decision, Decision)

    # 1. Recommendation matching
    assert decision.recommendation.value == exp["recommendation"], (
        f"[{scenario['id']}] Expected recommendation {exp['recommendation']}, got {decision.recommendation.value}"
    )

    # 2. Risk Level matching
    assert decision.risk_level.value == exp["risk_level"], (
        f"[{scenario['id']}] Expected risk level {exp['risk_level']}, got {decision.risk_level.value}"
    )

    # 3. Minimum Confidence matching
    min_confidence = exp.get("min_confidence", 0.0)
    assert decision.confidence >= min_confidence, (
        f"[{scenario['id']}] Confidence {decision.confidence} below expected minimum {min_confidence}"
    )

    # 4. Required Factor Code matching
    if "must_flag_factor" in exp:
        expected_code = exp["must_flag_factor"]
        actual_codes = [f.code for f in decision.factors]
        assert expected_code in actual_codes, (
            f"[{scenario['id']}] Expected factor code '{expected_code}' not found in {actual_codes}"
        )

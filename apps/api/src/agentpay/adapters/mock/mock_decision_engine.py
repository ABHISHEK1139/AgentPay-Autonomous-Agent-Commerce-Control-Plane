"""Deterministic mock decision engine for local development and hermetic testing (§28, §162, §163).

Provides predictable advisory risk scoring without external model calls or API keys.
"""

from __future__ import annotations

import uuid

from agentpay.application.ports.approval_port import DecisionEngine
from agentpay.domain.decision import (
    Decision,
    DecisionContext,
    DecisionFactor,
    DecisionRecommendation,
    RiskLevel,
)


class MockDecisionEngine(DecisionEngine):
    """Deterministic implementation of DecisionEngine for testing and demo mode."""

    def evaluate(self, context: DecisionContext) -> Decision:
        decision_id = f"dec_{uuid.uuid4().hex[:16]}"
        amount = context.amount

        if amount is None or amount.minor_units <= 200_000:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.ALLOW,
                risk_level=RiskLevel.LOW,
                confidence=0.95,
                factors=(
                    DecisionFactor(
                        code="WITHIN_AUTONOMOUS_LIMIT",
                        description="Amount is within autonomous ceiling",
                        weight=1.0,
                    ),
                ),
                model_provider="mock",
                model_version="mock-v1",
            )
        elif 200_000 < amount.minor_units <= 500_000:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.REVIEW,
                risk_level=RiskLevel.MEDIUM,
                confidence=0.85,
                factors=(
                    DecisionFactor(
                        code="MODERATE_VALUE_TRANSACTION",
                        description="Transaction value requires standard review",
                        weight=0.8,
                    ),
                ),
                model_provider="mock",
                model_version="mock-v1",
            )
        elif 500_000 < amount.minor_units <= 2_000_000:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.REVIEW,
                risk_level=RiskLevel.HIGH,
                confidence=0.80,
                factors=(
                    DecisionFactor(
                        code="HIGH_VALUE_TRANSACTION",
                        description="Transaction value requires senior approval",
                        weight=0.9,
                    ),
                ),
                model_provider="mock",
                model_version="mock-v1",
            )
        else:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.DENY,
                risk_level=RiskLevel.CRITICAL,
                confidence=0.99,
                factors=(
                    DecisionFactor(
                        code="EXCESSIVE_TRANSACTION_VALUE",
                        description="Transaction value exceeds maximum policy threshold",
                        weight=1.0,
                    ),
                ),
                model_provider="mock",
                model_version="mock-v1",
            )

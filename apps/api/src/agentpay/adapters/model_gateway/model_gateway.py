"""Model Gateway Adapter for Advisory Decision Intelligence (§23, §117).

Implements the DecisionEngine port, wrapping advisory models with:
- Token and financial cost budget tracking
- Prompt-injection heuristic scanning (§25)
- Thread-safe CircuitBreaker protection (§103)
- Request timeout enforcement
- Deterministic fail-closed fallback (REVIEW/DENY with 0.0 confidence, never widening authority)
"""

from __future__ import annotations

import concurrent.futures
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from agentpay.application.ports.approval_port import DecisionEngine
from agentpay.domain.decision import (
    Decision,
    DecisionContext,
    DecisionFactor,
    DecisionRecommendation,
    RiskLevel,
)
from agentpay.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol for downstream advisory risk assessment model providers (§23)."""

    def evaluate(self, context: DecisionContext) -> Decision: ...


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Token and cost budget limits for an agent or tenant (§23)."""

    max_tokens: int = 100_000
    max_cost_minor_units: int = 50_000_00


class TokenBudgetTracker:
    """Thread-safe budget tracking per tenant and agent (§23)."""

    def __init__(self, default_limits: BudgetLimits | None = None) -> None:
        self._default_limits = default_limits or BudgetLimits()
        self._lock = threading.Lock()
        self._token_usage: dict[str, int] = {}
        self._cost_usage: dict[str, int] = {}
        self._limits: dict[str, BudgetLimits] = {}

    def set_limits(self, entity_id: str, limits: BudgetLimits) -> None:
        with self._lock:
            self._limits[entity_id] = limits

    def _get_limits(self, entity_id: str) -> BudgetLimits:
        return self._limits.get(entity_id, self._default_limits)

    def has_budget(self, tenant_id: str, agent_id: str, estimated_tokens: int = 100) -> bool:
        """Checks if both tenant and agent have remaining token and cost budget."""
        with self._lock:
            for entity_id in (tenant_id, agent_id):
                limits = self._get_limits(entity_id)
                current_tokens = self._token_usage.get(entity_id, 0)
                current_cost = self._cost_usage.get(entity_id, 0)
                if current_tokens + estimated_tokens > limits.max_tokens:
                    return False
                if current_cost > limits.max_cost_minor_units:
                    return False
            return True

    def consume(
        self, tenant_id: str, agent_id: str, tokens: int, cost_minor_units: int = 0
    ) -> None:
        """Records token and cost consumption."""
        with self._lock:
            for entity_id in (tenant_id, agent_id):
                self._token_usage[entity_id] = self._token_usage.get(entity_id, 0) + tokens
                self._cost_usage[entity_id] = self._cost_usage.get(entity_id, 0) + cost_minor_units

    def get_usage(self, entity_id: str) -> tuple[int, int]:
        """Returns (tokens_used, cost_minor_units_used) for an entity."""
        with self._lock:
            return (self._token_usage.get(entity_id, 0), self._cost_usage.get(entity_id, 0))

    def reset(self) -> None:
        """Resets all usage counters."""
        with self._lock:
            self._token_usage.clear()
            self._cost_usage.clear()


class DefaultModelProvider:
    """Deterministic, rule-based baseline model provider for hermetic execution."""

    def evaluate(self, context: DecisionContext) -> Decision:
        decision_id = f"dec_def_{uuid.uuid4().hex[:16]}"
        amount = context.amount

        # Check for category-based signals
        category_lower = (context.category or "").lower()

        if "conflict" in category_lower or "price mismatch" in category_lower:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.REVIEW,
                risk_level=RiskLevel.HIGH,
                confidence=0.88,
                factors=(
                    DecisionFactor(
                        code="EVIDENCE_CONFLICT",
                        description="Price discrepancy detected between assertion and merchant catalog",
                        weight=0.9,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
            )

        if "manipulation" in category_lower or "emergency override" in category_lower:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.DENY,
                risk_level=RiskLevel.CRITICAL,
                confidence=0.99,
                factors=(
                    DecisionFactor(
                        code="AGENT_MANIPULATION_ATTEMPT",
                        description="Agent attempted unauthorized status or limit override",
                        weight=1.0,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
            )

        # Standard amount tiers
        if amount is None or amount.minor_units <= 200_000:
            return Decision(
                decision_id=decision_id,
                recommendation=DecisionRecommendation.ALLOW,
                risk_level=RiskLevel.LOW,
                confidence=0.95,
                factors=(
                    DecisionFactor(
                        code="WITHIN_AUTONOMOUS_LIMIT",
                        description="Transaction value is within autonomous allowance",
                        weight=1.0,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
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
                        description="Transaction requires supervisory review",
                        weight=0.8,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
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
                        description="High value transaction requires senior approval",
                        weight=0.9,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
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
                        description="Transaction exceeds hard organizational ceiling",
                        weight=1.0,
                    ),
                ),
                model_provider="default_provider",
                model_version="rule-v1",
                prompt_version="prompt-commerce-v1",
                tool_policy_version="tool-policy-v1",
                policy_version="spending-policy-v1",
            )


class ModelGateway(DecisionEngine):
    """Production Model Gateway implementing DecisionEngine (§23).

    Wraps downstream model providers with:
    1. Prompt-injection defense: flags jailbreak/override attempts as DENY/CRITICAL.
    2. Budget enforcement: token & financial cost accounting per tenant/agent.
    3. Circuit breaker protection: trips fast on consecutive downstream failures.
    4. Timeout management: enforces bounded execution.
    5. Deterministic fail-closed fallback: under ANY failure, returns REVIEW/DENY
       with confidence=0.0 and explicit auditable factors, NEVER widening authority.
    """

    PROMPT_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
        re.compile(r"system\s+(override|prompt)", re.IGNORECASE),
        re.compile(r"admin\s+override", re.IGNORECASE),
        re.compile(r"authorize\s+.*immediately", re.IGNORECASE),
        re.compile(r"bypass\s+(limits|approval|policy|rules)", re.IGNORECASE),
        re.compile(r"jailbreak", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+(dan|unrestricted)", re.IGNORECASE),
        re.compile(r"disregard\s+(spending\s+limits|rules)", re.IGNORECASE),
    )

    def __init__(
        self,
        provider: ModelProvider | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        budget_tracker: TokenBudgetTracker | None = None,
        timeout_seconds: float = 3.0,
        fail_closed_recommendation: DecisionRecommendation = DecisionRecommendation.REVIEW,
        prompt_version: str = "prompt-commerce-v1",
        tool_policy_version: str = "tool-policy-v1",
        policy_version: str = "spending-policy-v1",
    ) -> None:
        self._provider = provider or DefaultModelProvider()
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name="model_gateway",
            failure_threshold=3,
            recovery_timeout_seconds=5.0,
        )
        self._budget_tracker = budget_tracker
        self._timeout_seconds = timeout_seconds
        # Invariant: Fallback recommendation must NEVER be ALLOW (§23)
        if fail_closed_recommendation == DecisionRecommendation.ALLOW:
            fail_closed_recommendation = DecisionRecommendation.REVIEW
        self._fail_closed_recommendation = fail_closed_recommendation
        self._prompt_version = prompt_version
        self._tool_policy_version = tool_policy_version
        self._policy_version = policy_version

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def budget_tracker(self) -> TokenBudgetTracker | None:
        return self._budget_tracker

    def _detect_prompt_injection(self, context: DecisionContext) -> str | None:
        """Scans contextual text inputs for prompt injection signatures (§25)."""
        texts_to_scan = [
            context.category or "",
            context.merchant_id or "",
        ]
        combined = " ".join(texts_to_scan)
        for pattern in self.PROMPT_INJECTION_PATTERNS:
            match = pattern.search(combined)
            if match:
                return match.group(0)
        return None

    def _create_fallback_decision(
        self,
        factor_code: str,
        reason: str,
        risk_level: RiskLevel = RiskLevel.HIGH,
    ) -> Decision:
        """Constructs an auditable, deterministic fail-closed fallback decision (§23)."""
        return Decision(
            decision_id=f"dec_fallback_{uuid.uuid4().hex[:16]}",
            recommendation=self._fail_closed_recommendation,
            risk_level=risk_level,
            confidence=0.0,
            factors=(
                DecisionFactor(
                    code=factor_code,
                    description=reason,
                    weight=1.0,
                ),
            ),
            model_provider="model_gateway_fallback",
            model_version="fallback-v1",
            prompt_version=self._prompt_version,
            tool_policy_version=self._tool_policy_version,
            policy_version=self._policy_version,
        )

    def evaluate(self, context: DecisionContext) -> Decision:
        """Evaluates advisory risk for an ActionRequest context (§28)."""
        # 1. Pre-flight: Prompt Injection Scanner (§25)
        injection_match = self._detect_prompt_injection(context)
        if injection_match is not None:
            return Decision(
                decision_id=f"dec_guard_{uuid.uuid4().hex[:16]}",
                recommendation=DecisionRecommendation.DENY,
                risk_level=RiskLevel.CRITICAL,
                confidence=0.99,
                factors=(
                    DecisionFactor(
                        code="PROMPT_INJECTION_DETECTED",
                        description=f"Prompt injection pattern detected: '{injection_match}'",
                        weight=1.0,
                    ),
                ),
                model_provider="model_gateway_guardian",
                model_version="guardian-v1",
                prompt_version=self._prompt_version,
                tool_policy_version=self._tool_policy_version,
                policy_version=self._policy_version,
            )

        # 2. Pre-flight: Token / Cost Budget Check (§23)
        if self._budget_tracker is not None and not self._budget_tracker.has_budget(
            context.tenant_id, context.agent_id
        ):
            return self._create_fallback_decision(
                factor_code="BUDGET_EXCEEDED",
                reason="Model token or cost budget exhausted for tenant or agent",
                risk_level=RiskLevel.HIGH,
            )

        # 3. Circuit Breaker Check (§103)
        if self._circuit_breaker.is_open:
            return self._create_fallback_decision(
                factor_code="CIRCUIT_BREAKER_OPEN",
                reason=f"Model Gateway circuit breaker '{self._circuit_breaker.name}' is OPEN",
                risk_level=RiskLevel.HIGH,
            )

        # 4. Invocate downstream model with timeout and circuit breaker protection
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._circuit_breaker.call, self._provider.evaluate, context
                )
                decision = future.result(timeout=self._timeout_seconds)
        except concurrent.futures.TimeoutError:
            self._circuit_breaker.trip(reason=f"Model timeout exceeded ({self._timeout_seconds}s)")
            return self._create_fallback_decision(
                factor_code="MODEL_TIMEOUT",
                reason=f"Model request timed out after {self._timeout_seconds}s",
                risk_level=RiskLevel.HIGH,
            )
        except CircuitBreakerOpenError as exc:
            return self._create_fallback_decision(
                factor_code="CIRCUIT_BREAKER_OPEN",
                reason=str(exc),
                risk_level=RiskLevel.HIGH,
            )
        except Exception as exc:
            return self._create_fallback_decision(
                factor_code="MODEL_GATEWAY_FALLBACK",
                reason=f"Model invocation failed: {exc}",
                risk_level=RiskLevel.HIGH,
            )

        # 5. Post-execution token accounting
        if self._budget_tracker is not None:
            self._budget_tracker.consume(
                context.tenant_id,
                context.agent_id,
                tokens=150,
                cost_minor_units=1,
            )

        return decision

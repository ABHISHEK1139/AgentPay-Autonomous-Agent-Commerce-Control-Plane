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


class BudgetOverReleaseError(ValueError):
    """Raised when attempting to release more tokens or cost than currently reserved or tracked (§23)."""


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

    def _get_entity_limits(self, namespaced_key: str, raw_id: str) -> BudgetLimits:
        if namespaced_key in self._limits:
            return self._limits[namespaced_key]
        return self._limits.get(raw_id, self._default_limits)

    def has_budget(
        self,
        tenant_id: str,
        agent_id: str,
        estimated_tokens: int = 100,
        estimated_cost: int = 1,
    ) -> bool:
        """Checks if both tenant and agent have remaining token and cost budget."""
        with self._lock:
            tenant_key = f"tenant:{tenant_id}"
            agent_key = f"agent:{tenant_id}:{agent_id}"
            t_limits = self._get_entity_limits(tenant_key, tenant_id)
            a_limits = self._get_entity_limits(agent_key, agent_id)

            t_tokens = self._token_usage.get(tenant_key, 0)
            t_cost = self._cost_usage.get(tenant_key, 0)
            if t_tokens + estimated_tokens > t_limits.max_tokens:
                return False
            if t_cost + estimated_cost > t_limits.max_cost_minor_units:
                return False

            a_tokens = self._token_usage.get(agent_key, 0)
            a_cost = self._cost_usage.get(agent_key, 0)
            if a_tokens + estimated_tokens > a_limits.max_tokens:
                return False
            return a_cost + estimated_cost <= a_limits.max_cost_minor_units

    def acquire(
        self,
        tenant_id: str,
        agent_id: str,
        estimated_tokens: int = 150,
        estimated_cost: int = 1,
    ) -> bool:
        """Atomically checks limits and reserves budget under lock (§23)."""
        with self._lock:
            tenant_key = f"tenant:{tenant_id}"
            agent_key = f"agent:{tenant_id}:{agent_id}"
            t_limits = self._get_entity_limits(tenant_key, tenant_id)
            a_limits = self._get_entity_limits(agent_key, agent_id)

            t_tokens = self._token_usage.get(tenant_key, 0)
            t_cost = self._cost_usage.get(tenant_key, 0)
            if t_tokens + estimated_tokens > t_limits.max_tokens:
                return False
            if t_cost + estimated_cost > t_limits.max_cost_minor_units:
                return False

            a_tokens = self._token_usage.get(agent_key, 0)
            a_cost = self._cost_usage.get(agent_key, 0)
            if a_tokens + estimated_tokens > a_limits.max_tokens:
                return False
            if a_cost + estimated_cost > a_limits.max_cost_minor_units:
                return False

            # Atomically reserve budget
            self._token_usage[tenant_key] = t_tokens + estimated_tokens
            self._cost_usage[tenant_key] = t_cost + estimated_cost
            self._token_usage[agent_key] = a_tokens + estimated_tokens
            self._cost_usage[agent_key] = a_cost + estimated_cost
            return True

    def release(
        self,
        tenant_id: str,
        agent_id: str,
        tokens: int = 150,
        cost_minor_units: int = 1,
    ) -> None:
        """Rollbacks reserved tokens and cost on failure under lock (§23)."""
        with self._lock:
            tenant_key = f"tenant:{tenant_id}"
            agent_key = f"agent:{tenant_id}:{agent_id}"

            t_tokens = self._token_usage.get(tenant_key, 0)
            t_cost = self._cost_usage.get(tenant_key, 0)
            a_tokens = self._token_usage.get(agent_key, 0)
            a_cost = self._cost_usage.get(agent_key, 0)

            if tokens > t_tokens or tokens > a_tokens:
                raise BudgetOverReleaseError(
                    f"Cannot release {tokens} tokens: exceeds tracked usage "
                    f"(tenant={t_tokens}, agent={a_tokens})"
                )
            if cost_minor_units > t_cost or cost_minor_units > a_cost:
                raise BudgetOverReleaseError(
                    f"Cannot release {cost_minor_units} cost units: exceeds tracked usage "
                    f"(tenant={t_cost}, agent={a_cost})"
                )

            self._token_usage[tenant_key] = t_tokens - tokens
            self._cost_usage[tenant_key] = t_cost - cost_minor_units
            self._token_usage[agent_key] = a_tokens - tokens
            self._cost_usage[agent_key] = a_cost - cost_minor_units

    def consume(
        self, tenant_id: str, agent_id: str, tokens: int, cost_minor_units: int = 0
    ) -> None:
        """Records token and cost consumption without double consumption (§23)."""
        with self._lock:
            tenant_key = f"tenant:{tenant_id}"
            agent_key = f"agent:{tenant_id}:{agent_id}"

            self._token_usage[tenant_key] = self._token_usage.get(tenant_key, 0) + tokens
            self._cost_usage[tenant_key] = self._cost_usage.get(tenant_key, 0) + cost_minor_units
            self._token_usage[agent_key] = self._token_usage.get(agent_key, 0) + tokens
            self._cost_usage[agent_key] = self._cost_usage.get(agent_key, 0) + cost_minor_units

    def get_usage(self, entity_id: str) -> tuple[int, int]:
        """Returns (tokens_used, cost_minor_units_used) for an entity (§23).

        Supports exact match, tenant keys ('tenant:{entity_id}'), and agent suffixes.
        """
        with self._lock:
            if entity_id in self._token_usage:
                return (self._token_usage[entity_id], self._cost_usage.get(entity_id, 0))

            tenant_key = f"tenant:{entity_id}"
            if tenant_key in self._token_usage:
                return (self._token_usage[tenant_key], self._cost_usage.get(tenant_key, 0))

            matching_agent_keys = [
                k
                for k in self._token_usage
                if k.startswith("agent:")
                and (k.endswith(f":{entity_id}") or k == f"agent:{entity_id}")
            ]
            if matching_agent_keys:
                total_t = sum(self._token_usage.get(k, 0) for k in matching_agent_keys)
                total_c = sum(self._cost_usage.get(k, 0) for k in matching_agent_keys)
                return (total_t, total_c)

            return (0, 0)

    def reset(self) -> None:
        """Resets all usage counters under lock (§23)."""
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
        self._default_circuit_breaker = circuit_breaker
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._cb_lock = threading.Lock()
        self._budget_tracker = budget_tracker
        self._timeout_seconds = timeout_seconds
        # Invariant: Fallback recommendation must NEVER be ALLOW (§23)
        if fail_closed_recommendation == DecisionRecommendation.ALLOW:
            fail_closed_recommendation = DecisionRecommendation.REVIEW
        self._fail_closed_recommendation = fail_closed_recommendation
        self._prompt_version = prompt_version
        self._tool_policy_version = tool_policy_version
        self._policy_version = policy_version

    def _get_circuit_breaker(self, tenant_id: str) -> CircuitBreaker:
        """Retrieves or creates a tenant-isolated CircuitBreaker (§23, §103)."""
        if self._default_circuit_breaker is not None:
            return self._default_circuit_breaker
        with self._cb_lock:
            if tenant_id not in self._circuit_breakers:
                self._circuit_breakers[tenant_id] = CircuitBreaker(
                    name=f"model_gateway:{tenant_id}",
                    failure_threshold=3,
                    recovery_timeout_seconds=5.0,
                )
            return self._circuit_breakers[tenant_id]

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        if self._default_circuit_breaker is not None:
            return self._default_circuit_breaker
        return self._get_circuit_breaker("default")

    @property
    def _circuit_breaker(self) -> CircuitBreaker:
        return self.circuit_breaker

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

        cb = self._get_circuit_breaker(context.tenant_id)

        # 2. Circuit Breaker Fast-Check (§103)
        if cb.is_open:
            return self._create_fallback_decision(
                factor_code="CIRCUIT_BREAKER_OPEN",
                reason=f"Model Gateway circuit breaker '{cb.name}' is OPEN",
                risk_level=RiskLevel.HIGH,
            )

        # 3. Pre-flight: Atomic Token / Cost Budget Reservation (§23)
        reserved = False
        if self._budget_tracker is not None:
            if not self._budget_tracker.acquire(
                context.tenant_id,
                context.agent_id,
                estimated_tokens=150,
                estimated_cost=1,
            ):
                return self._create_fallback_decision(
                    factor_code="BUDGET_EXCEEDED",
                    reason="Model token or cost budget exhausted for tenant or agent",
                    risk_level=RiskLevel.HIGH,
                )
            reserved = True

        # 4. Invocate downstream model with timeout and circuit breaker protection
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(cb.call, self._provider.evaluate, context)
                decision = future.result(timeout=self._timeout_seconds)
        except concurrent.futures.TimeoutError:
            cb.trip(reason=f"Model timeout exceeded ({self._timeout_seconds}s)")
            if reserved and self._budget_tracker is not None:
                self._budget_tracker.release(
                    context.tenant_id,
                    context.agent_id,
                    tokens=150,
                    cost_minor_units=1,
                )
            return self._create_fallback_decision(
                factor_code="MODEL_TIMEOUT",
                reason=f"Model request timed out after {self._timeout_seconds}s",
                risk_level=RiskLevel.HIGH,
            )
        except CircuitBreakerOpenError as exc:
            if reserved and self._budget_tracker is not None:
                self._budget_tracker.release(
                    context.tenant_id,
                    context.agent_id,
                    tokens=150,
                    cost_minor_units=1,
                )
            return self._create_fallback_decision(
                factor_code="CIRCUIT_BREAKER_OPEN",
                reason=str(exc),
                risk_level=RiskLevel.HIGH,
            )
        except Exception as exc:
            if reserved and self._budget_tracker is not None:
                self._budget_tracker.release(
                    context.tenant_id,
                    context.agent_id,
                    tokens=150,
                    cost_minor_units=1,
                )
            return self._create_fallback_decision(
                factor_code="MODEL_GATEWAY_FALLBACK",
                reason=f"Model invocation failed: {exc}",
                risk_level=RiskLevel.HIGH,
            )

        return decision

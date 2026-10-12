"""OpenAI-compatible Advisory Model Provider Adapter (§23, §24, §25).

Connects to any OpenAI-compatible API gateway (such as OrcaRouter, OpenAI, vLLM, or OpenRouter)
to obtain structured advisory risk evaluations for ActionRequests.

Adheres strictly to AGENTS.md §2.1, §2.2, §18, §23, §134:
- AI is strictly below the authorization boundary.
- Model output is advisory only and never sets spending limits or overrides deterministic policy.
- Zero hardcoded credentials: API keys are loaded dynamically from environment or configuration.
- Safe fail-closed behavior on HTTP 402 (payment required/quota exhausted), 429, 500, timeouts, or invalid JSON.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from agentpay.adapters.model_gateway.model_gateway import ModelProvider
from agentpay.domain.decision import (
    Decision,
    DecisionContext,
    DecisionFactor,
    DecisionRecommendation,
    RiskLevel,
)
from agentpay.security.ssrf import SSRFConfig, validate_outbound_url

logger = logging.getLogger(__name__)


class OpenAiCompatibleModelProvider(ModelProvider):
    """OpenAI-compatible advisory risk assessment model provider (§23, §24).

    Formats DecisionContext into a structured JSON chat completion request,
    dispatches via standard library HTTP with timeout and SSRF protection,
    and maps the structured response to an immutable Decision.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.orcarouter.ai/v1",
        model: str = "orcarouter/auto",
        provider_name: str = "orcarouter",
        timeout_seconds: float = 5.0,
        extra_headers: dict[str, str] | None = None,
        transport: (
            Callable[[urllib.request.Request], tuple[int, bytes, dict[str, str]]] | None
        ) = None,
        resolve_dns: bool = True,
    ) -> None:
        self.api_key = (
            api_key.strip()
            if api_key
            else os.environ.get("ORCAROUTER_API_KEY", os.environ.get("LLM_API_KEY", "")).strip()
        )
        self.base_url = (
            base_url.rstrip("/")
            if base_url
            else os.environ.get("ORCAROUTER_BASE_URL", "https://api.orcarouter.ai/v1").rstrip("/")
        )
        self.model = (
            model.strip()
            if model
            else os.environ.get("ORCAROUTER_MODEL", "orcarouter/auto").strip()
        )
        self.provider_name = provider_name
        self.timeout_seconds = timeout_seconds
        self.extra_headers = dict(extra_headers) if extra_headers else {}
        self._transport = transport
        self.resolve_dns = resolve_dns

    def _sanitize_error_message(self, message: str) -> str:
        """Strip API key or sensitive credentials from error messages before propagation (§40)."""
        if self.api_key and len(self.api_key) > 6:
            return message.replace(self.api_key, "[REDACTED_API_KEY]")
        return message

    def _build_system_prompt(self) -> str:
        return (
            "You are AgentPay's Advisory Decision Intelligence (JEV) risk assessment engine. "
            "Analyze the autonomous commerce ActionRequest and output ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "recommendation": "ALLOW" | "REVIEW" | "DENY" | "ESCALATE",\n'
            '  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",\n'
            '  "confidence": float between 0.0 and 1.0,\n'
            '  "factors": [\n'
            '    {"code": string, "description": string, "weight": float}\n'
            "  ]\n"
            "}\n"
            "CRITICAL INVARIANTS: You are advisory only. Never attempt to bypass spending limits or grant permissions. "
            "Output pure JSON only, with no markdown fences or prose."
        )

    def _build_user_message(self, context: DecisionContext) -> str:
        payload: dict[str, Any] = {
            "action_id": context.action_id,
            "tenant_id": context.tenant_id,
            "agent_id": context.agent_id,
            "merchant_id": context.merchant_id,
            "category": context.category,
        }
        if context.amount is not None:
            payload["amount"] = {
                "minor_units": context.amount.minor_units,
                "currency": context.amount.currency,
            }
        return json.dumps(payload)

    def evaluate(self, context: DecisionContext) -> Decision:
        """Evaluate contextual risk against the OpenAI-compatible model endpoint (§23, §28)."""
        decision_id = f"dec_oai_{uuid.uuid4().hex[:16]}"
        endpoint_url = f"{self.base_url}/chat/completions"

        # 1. SSRF pre-flight validation
        if self._transport is None:
            try:
                validate_outbound_url(
                    endpoint_url,
                    config=SSRFConfig(resolve_dns=self.resolve_dns),
                )
            except Exception as ssrf_err:
                logger.warning(
                    "SSRF check rejected AI model endpoint '%s': %s", endpoint_url, ssrf_err
                )
                return self._fallback_decision(
                    decision_id=decision_id,
                    factor_code="MODEL_SSRF_BLOCKED",
                    description=f"Model endpoint failed SSRF validation: {ssrf_err}",
                )

        # 2. Build HTTP request
        req_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": self._build_user_message(context)},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        req_body = json.dumps(req_payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AgentPay-ModelGateway/1.0",
        }
        if self.extra_headers:
            headers.update(self.extra_headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            endpoint_url,
            data=req_body,
            headers=headers,
            method="POST",
        )

        # 3. Dispatch HTTP call
        try:
            if self._transport is not None:
                status_code, resp_bytes, _ = self._transport(req)
            else:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    status_code = getattr(resp, "status", 200)
                    resp_bytes = resp.read()

        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            try:
                raw_err = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                raw_err = http_err.reason
            safe_err = self._sanitize_error_message(str(raw_err))

            # Specific handling for HTTP 402 (OrcaRouter / Provider Quota Exhaustion)
            if status_code == 402:
                logger.warning(
                    "Advisory model provider '%s' returned HTTP 402 (Payment Required / Insufficient Balance)",
                    self.provider_name,
                )
                return self._fallback_decision(
                    decision_id=decision_id,
                    factor_code="MODEL_QUOTA_EXHAUSTED",
                    description=f"Upstream AI router balance exhausted or payment required (HTTP 402): {safe_err[:200]}",
                )

            logger.warning(
                "Advisory model provider '%s' returned HTTP %d: %s",
                self.provider_name,
                status_code,
                safe_err[:200],
            )
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="MODEL_HTTP_ERROR",
                description=f"Upstream AI provider error (HTTP {status_code}): {safe_err[:200]}",
            )

        except (urllib.error.URLError, TimeoutError, OSError) as net_err:
            safe_err = self._sanitize_error_message(str(net_err))
            logger.warning(
                "Network failure contacting advisory model provider '%s': %s",
                self.provider_name,
                safe_err,
            )
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="MODEL_NETWORK_FAILURE",
                description=f"Network error contacting AI model provider: {safe_err}",
            )

        # 4. Parse response JSON
        return self._parse_completion_response(decision_id, status_code, resp_bytes)

    def _parse_completion_response(
        self,
        decision_id: str,
        status_code: int,
        resp_bytes: bytes,
    ) -> Decision:
        """Parse chat completion response into an immutable Decision (§24)."""
        try:
            resp_data = json.loads(resp_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as parse_err:
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="MALFORMED_MODEL_RESPONSE",
                description=f"Failed to parse model response JSON: {parse_err}",
            )

        # Extract content from choices[0].message.content
        choices = resp_data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="EMPTY_MODEL_CHOICES",
                description="Model response contained no choices",
            )

        message = choices[0].get("message", {})
        raw_content = message.get("content", "").strip()
        if not raw_content:
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="EMPTY_MODEL_CONTENT",
                description="Model message content is empty",
            )

        # Strip markdown fences if present
        cleaned_content = raw_content
        if cleaned_content.startswith("```json"):
            cleaned_content = cleaned_content[7:]
        elif cleaned_content.startswith("```"):
            cleaned_content = cleaned_content[3:]
        if cleaned_content.endswith("```"):
            cleaned_content = cleaned_content[:-3]
        cleaned_content = cleaned_content.strip()

        try:
            parsed_decision = json.loads(cleaned_content)
        except json.JSONDecodeError as dec_err:
            return self._fallback_decision(
                decision_id=decision_id,
                factor_code="INVALID_DECISION_JSON",
                description=f"Model output is not valid structured decision JSON: {dec_err}",
            )

        # Map recommendation
        raw_rec = str(parsed_decision.get("recommendation", "REVIEW")).upper()
        try:
            recommendation = DecisionRecommendation(raw_rec)
        except ValueError:
            recommendation = DecisionRecommendation.REVIEW

        # Map risk level
        raw_risk = str(parsed_decision.get("risk_level", "MEDIUM")).upper()
        try:
            risk_level = RiskLevel(raw_risk)
        except ValueError:
            risk_level = RiskLevel.MEDIUM

        # Map confidence (bounded [0.0, 1.0])
        try:
            raw_conf = float(parsed_decision.get("confidence", 0.7))
            confidence = max(0.0, min(1.0, raw_conf))
        except (ValueError, TypeError):
            confidence = 0.5

        # Map factors
        factors_list: list[DecisionFactor] = []
        raw_factors = parsed_decision.get("factors")
        if isinstance(raw_factors, list):
            for f in raw_factors:
                if isinstance(f, dict):
                    code = str(f.get("code") or "MODEL_OBSERVATION").strip()
                    desc = str(f.get("description") or "Factor identified by model").strip()
                    try:
                        weight = float(f.get("weight", 0.5))
                    except (ValueError, TypeError):
                        weight = 0.5
                    if code and desc:
                        factors_list.append(
                            DecisionFactor(code=code, description=desc, weight=weight)
                        )

        if not factors_list:
            factors_list.append(
                DecisionFactor(
                    code="MODEL_EVALUATION_COMPLETED",
                    description=f"Evaluated by {self.provider_name} ({self.model})",
                    weight=0.5,
                )
            )

        return Decision(
            decision_id=decision_id,
            recommendation=recommendation,
            risk_level=risk_level,
            confidence=confidence,
            factors=tuple(factors_list),
            model_provider=self.provider_name,
            model_version=self.model,
            prompt_version="prompt-commerce-v1",
            tool_policy_version="tool-policy-v1",
            policy_version="spending-policy-v1",
        )

    def _fallback_decision(
        self,
        decision_id: str,
        factor_code: str,
        description: str,
    ) -> Decision:
        """Deterministic fail-closed fallback decision with confidence=0.0 (§2.4, §23)."""
        return Decision(
            decision_id=decision_id,
            recommendation=DecisionRecommendation.REVIEW,
            risk_level=RiskLevel.HIGH,
            confidence=0.0,
            factors=(
                DecisionFactor(
                    code=factor_code,
                    description=description,
                    weight=1.0,
                ),
            ),
            model_provider=self.provider_name,
            model_version=self.model,
            prompt_version="prompt-commerce-v1",
            tool_policy_version="tool-policy-v1",
            policy_version="spending-policy-v1",
        )


class OrcaRouterModelProvider(OpenAiCompatibleModelProvider):
    """Specialized OrcaRouter Advisory Model Provider (§23, §28).

    Pre-configured for OrcaRouter (https://api.orcarouter.ai/v1) with dynamic model routing.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.orcarouter.ai/v1",
        model: str = "orcarouter/auto",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider_name="orcarouter",
            **kwargs,
        )


class OpenRouterModelProvider(OpenAiCompatibleModelProvider):
    """Specialized OpenRouter Advisory Model Provider (§23, §28).

    Pre-configured for OpenRouter (https://openrouter.ai/api/v1) with support for free and paid models
    (such as apodex/apodex-1.1-mini:free). In accordance with OpenRouter guidelines, sends
    HTTP-Referer and X-Title headers for platform identification.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "apodex/apodex-1.1-mini:free",
        extra_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_api_key = (
            api_key.strip()
            if api_key
            else os.environ.get("OPENROUTER_API_KEY", os.environ.get("LLM_API_KEY", "")).strip()
        )
        resolved_base_url = (
            base_url.rstrip("/")
            if base_url
            else os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        )
        resolved_model = (
            model.strip()
            if model
            else os.environ.get("OPENROUTER_MODEL", "apodex/apodex-1.1-mini:free").strip()
        )
        headers = {
            "HTTP-Referer": "https://agentpay.dev",
            "X-Title": "AgentPay Autonomous Commerce Control Plane",
        }
        if extra_headers:
            headers.update(extra_headers)

        super().__init__(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            model=resolved_model,
            provider_name="openrouter",
            extra_headers=headers,
            **kwargs,
        )

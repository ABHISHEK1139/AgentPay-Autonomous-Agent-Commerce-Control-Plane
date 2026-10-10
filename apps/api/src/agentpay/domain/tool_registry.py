"""Fixed, permissioned tool registry (§26, §27).

A model cannot invent tools. Every callable surface is declared here in advance with
its input/output schema, required capability/permission, risk tier/level, permitted callers,
rate limit, and audit behaviour. An invocation that is not in this registry does not exist.

This module is pure domain logic: no model, no I/O, no dynamic dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, overload

from agentpay.domain.errors import (
    InvalidCommerceError,
    ToolCallerNotAllowedError,
    ToolPermissionDeniedError,
    UnauthorizedToolAccessError,
    UnknownToolError,
)
from agentpay.domain.identity import Agent
from agentpay.domain.identity_token import PrincipalType


class ToolRiskTier(StrEnum):
    """Coarse risk tier required for every registered tool (§26, §27)."""

    READ_ONLY = "READ_ONLY"
    FINANCIAL_MUTATING = "FINANCIAL_MUTATING"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    SYSTEM = "SYSTEM"


class ToolRiskLevel(StrEnum):
    """Risk classification required for every registered tool (§27) - legacy compatibility."""

    READ = "READ"
    WRITE_LOW = "WRITE_LOW"
    WRITE_MEDIUM = "WRITE_MEDIUM"
    FINANCIAL = "FINANCIAL"
    DESTRUCTIVE = "DESTRUCTIVE"
    SECURITY_SENSITIVE = "SECURITY_SENSITIVE"


class ToolAuditBehavior(StrEnum):
    """Whether and how a tool invocation must be audited (§26, §45)."""

    NONE = "NONE"
    LOG = "LOG"
    ALWAYS = "ALWAYS"


_HIGH_RISK_BEHAVIOR: Final[dict[ToolRiskLevel, ToolAuditBehavior]] = {
    ToolRiskLevel.READ: ToolAuditBehavior.LOG,
    ToolRiskLevel.WRITE_LOW: ToolAuditBehavior.LOG,
    ToolRiskLevel.WRITE_MEDIUM: ToolAuditBehavior.ALWAYS,
    ToolRiskLevel.FINANCIAL: ToolAuditBehavior.ALWAYS,
    ToolRiskLevel.DESTRUCTIVE: ToolAuditBehavior.ALWAYS,
    ToolRiskLevel.SECURITY_SENSITIVE: ToolAuditBehavior.ALWAYS,
}

_ALL_CALLERS: Final[frozenset[PrincipalType]] = frozenset(PrincipalType)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A registered tool definition and capability authorization requirement (§26, §27)."""

    name: str
    description: str
    risk_tier: ToolRiskTier
    required_capability: str
    enabled: bool = True
    rate_limit_per_minute: int = 60

    def __post_init__(self) -> None:
        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
            ("required_capability", self.required_capability),
        ):
            val_obj: object = value
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidCommerceError(f"tool {field_name} must be a non-empty string")
        if not isinstance(self.risk_tier, ToolRiskTier):
            raise InvalidCommerceError(f"invalid tool risk_tier: {self.risk_tier!r}")
        if not isinstance(self.enabled, bool):
            raise InvalidCommerceError(f"tool enabled must be a boolean, got {self.enabled!r}")
        rl_obj: object = self.rate_limit_per_minute
        if isinstance(rl_obj, bool) or self.rate_limit_per_minute <= 0:
            raise InvalidCommerceError("rate_limit_per_minute must be a positive integer")

    @property
    def is_high_risk(self) -> bool:
        return self.risk_tier in (
            ToolRiskTier.FINANCIAL_MUTATING,
            ToolRiskTier.ADMINISTRATIVE,
            ToolRiskTier.SYSTEM,
        )

    @property
    def required_permission(self) -> str:
        """Alias for backward compatibility with permission-based checks."""
        return self.required_capability

    @property
    def risk_level(self) -> ToolRiskLevel:
        """Backward compatibility mapping to ToolRiskLevel."""
        if self.risk_tier == ToolRiskTier.READ_ONLY:
            return ToolRiskLevel.READ
        if self.risk_tier == ToolRiskTier.FINANCIAL_MUTATING:
            return ToolRiskLevel.FINANCIAL
        return ToolRiskLevel.SECURITY_SENSITIVE


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A single registered tool and everything required to authorize calling it (legacy model)."""

    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    required_permission: str
    risk_level: ToolRiskLevel
    rate_limit_per_minute: int
    allowed_callers: frozenset[PrincipalType] = _ALL_CALLERS
    audit_behavior: ToolAuditBehavior | None = None

    def __post_init__(self) -> None:
        for name, val in (
            ("name", self.name),
            ("description", self.description),
            ("required_permission", self.required_permission),
        ):
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise InvalidCommerceError(f"tool {name} must be a non-empty string")
        in_obj: object = self.input_schema
        out_obj: object = self.output_schema
        if not isinstance(in_obj, dict) or not isinstance(out_obj, dict):
            raise InvalidCommerceError("tool schemas must be dicts")
        if isinstance(self.rate_limit_per_minute, bool) or self.rate_limit_per_minute <= 0:
            raise InvalidCommerceError("tool rate_limit_per_minute must be a positive int")

    @property
    def effective_audit_behavior(self) -> ToolAuditBehavior:
        if self.audit_behavior is not None:
            return self.audit_behavior
        return _HIGH_RISK_BEHAVIOR[self.risk_level]

    @property
    def requires_audit(self) -> bool:
        return self.effective_audit_behavior is not ToolAuditBehavior.NONE

    @property
    def is_high_risk(self) -> bool:
        return self.risk_level in (
            ToolRiskLevel.WRITE_MEDIUM,
            ToolRiskLevel.FINANCIAL,
            ToolRiskLevel.DESTRUCTIVE,
            ToolRiskLevel.SECURITY_SENSITIVE,
        )

    @property
    def required_capability(self) -> str:
        """Alias for capability-based authorization."""
        return self.required_permission

    @property
    def enabled(self) -> bool:
        return True

    @property
    def risk_tier(self) -> ToolRiskTier:
        """Mapping from legacy ToolRiskLevel to ToolRiskTier."""
        if self.risk_level == ToolRiskLevel.READ:
            return ToolRiskTier.READ_ONLY
        if self.risk_level in (
            ToolRiskLevel.WRITE_LOW,
            ToolRiskLevel.WRITE_MEDIUM,
            ToolRiskLevel.FINANCIAL,
        ):
            return ToolRiskTier.FINANCIAL_MUTATING
        return ToolRiskTier.ADMINISTRATIVE


class ToolRegistry:
    """Immutable registry. Membership cannot be changed at runtime (§26, §27)."""

    def __init__(self, tools: tuple[ToolDefinition | ToolSpec, ...] = ()) -> None:
        by_name: dict[str, ToolDefinition | ToolSpec] = {}
        for item in tools:
            if item.name in by_name:
                raise InvalidCommerceError(f"duplicate tool registration: {item.name}")
            by_name[item.name] = item
        self._tools: Final[dict[str, ToolDefinition | ToolSpec]] = by_name

    def get(self, name: str) -> ToolDefinition | ToolSpec:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"Tool '{name}' is not registered in the tool registry")
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def names_for_risk(self, risk: ToolRiskLevel | ToolRiskTier) -> tuple[str, ...]:
        matching: list[str] = []
        for n, item in self._tools.items():
            matches_def = isinstance(item, ToolDefinition) and (
                (isinstance(risk, ToolRiskTier) and item.risk_tier is risk)
                or (isinstance(risk, ToolRiskLevel) and item.risk_level is risk)
            )
            matches_spec = isinstance(item, ToolSpec) and (
                (isinstance(risk, ToolRiskLevel) and item.risk_level is risk)
                or (isinstance(risk, ToolRiskTier) and item.risk_tier is risk)
            )
            if matches_def or matches_spec:
                matching.append(n)
        return tuple(sorted(matching))

    @overload
    def authorize(self, name: str, agent: Agent) -> ToolDefinition | ToolSpec: ...

    @overload
    def authorize(
        self,
        name: str,
        agent: frozenset[str] | None = None,
        caller_type: PrincipalType | None = None,
        *,
        granted_permissions: frozenset[str] | None = None,
    ) -> ToolDefinition | ToolSpec: ...

    def authorize(
        self,
        name: str,
        agent: Agent | frozenset[str] | None = None,
        caller_type: PrincipalType | None = None,
        *,
        granted_permissions: frozenset[str] | None = None,
    ) -> ToolDefinition | ToolSpec:
        """Resolve a tool and enforce capability, permission, and caller restrictions, or raise (§26, §27)."""
        tool = self.get(name)  # raises UnknownToolError if unregistered
        if isinstance(agent, Agent):
            if isinstance(tool, ToolDefinition):
                if not tool.enabled:
                    raise UnauthorizedToolAccessError(f"Tool '{name}' is disabled")
                if not agent.has_capability(tool.required_capability):
                    raise UnauthorizedToolAccessError(
                        f"Agent '{agent.agent_id}' lacks required capability '{tool.required_capability}' for tool '{name}'"
                    )
                return tool
            elif isinstance(tool, ToolSpec):
                if not agent.has_capability(tool.required_permission):
                    raise UnauthorizedToolAccessError(
                        f"Agent '{agent.agent_id}' lacks required capability '{tool.required_permission}' for tool '{name}'"
                    )
                return tool

        # Legacy caller authorization with permissions and caller_type
        effective_perms = granted_permissions
        if effective_perms is None and isinstance(agent, (frozenset, set)):
            effective_perms = frozenset(agent)
        elif effective_perms is None:
            effective_perms = frozenset()

        required_perm = (
            tool.required_capability
            if isinstance(tool, ToolDefinition)
            else tool.required_permission
        )
        if required_perm not in effective_perms:
            raise ToolPermissionDeniedError(f"tool '{name}' requires permission '{required_perm}'")
        if (
            caller_type is not None
            and isinstance(tool, ToolSpec)
            and caller_type not in tool.allowed_callers
        ):
            raise ToolCallerNotAllowedError(
                f"tool '{name}' may not be called by a {caller_type.value} principal"
            )
        return tool


def build_default_tool_registry() -> ToolRegistry:
    """The fixed commerce tool surface described in §26, §27.

    Nothing here executes anything; it is the authorization and classification surface.
    Every financial or administrative tool is gated separately from read tools so an
    agent cannot perform actions without explicit capability grants.
    """
    return ToolRegistry(
        (
            ToolDefinition(
                name="search_products",
                description="Search the product catalogue.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="catalog.search",
                rate_limit_per_minute=120,
            ),
            ToolDefinition(
                name="get_product",
                description="Fetch a single product record.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="catalog.read",
                rate_limit_per_minute=240,
            ),
            ToolDefinition(
                name="compare_products",
                description="Compare candidate products.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="catalog.compare",
                rate_limit_per_minute=60,
            ),
            ToolDefinition(
                name="get_evidence",
                description="Fetch product evidence and provenance.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="evidence.read",
                rate_limit_per_minute=120,
            ),
            ToolDefinition(
                name="check_inventory",
                description="Check current stock availability.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="inventory.read",
                rate_limit_per_minute=120,
            ),
            ToolDefinition(
                name="calculate_total",
                description="Compute cart total amount.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="cart.read",
                rate_limit_per_minute=60,
            ),
            ToolDefinition(
                name="check_payment_status",
                description="Inspect payment status.",
                risk_tier=ToolRiskTier.READ_ONLY,
                required_capability="payment.read",
                rate_limit_per_minute=120,
            ),
            ToolDefinition(
                name="create_cart",
                description="Initialize shopping cart.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="cart.create",
                rate_limit_per_minute=30,
            ),
            ToolDefinition(
                name="request_approval",
                description="Create human approval request.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="approval.request",
                rate_limit_per_minute=20,
            ),
            ToolDefinition(
                name="prepare_checkout",
                description="Prepare order draft for payment.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="checkout.prepare",
                rate_limit_per_minute=20,
            ),
            ToolDefinition(
                name="request_payment",
                description="Request payment execution.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="payment.request",
                rate_limit_per_minute=10,
            ),
            ToolDefinition(
                name="create_order",
                description="Confirm and create final order.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="order.create",
                rate_limit_per_minute=20,
            ),
            ToolDefinition(
                name="refund_payment",
                description="Refund captured payment.",
                risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
                required_capability="payment.refund",
                rate_limit_per_minute=5,
            ),
            ToolDefinition(
                name="revoke_agent",
                description="Revoke agent credential.",
                risk_tier=ToolRiskTier.ADMINISTRATIVE,
                required_capability="agent.revoke",
                rate_limit_per_minute=5,
            ),
            ToolDefinition(
                name="pause_agent",
                description="Temporarily suspend agent.",
                risk_tier=ToolRiskTier.ADMINISTRATIVE,
                required_capability="agent.pause",
                rate_limit_per_minute=5,
            ),
            ToolDefinition(
                name="system_health",
                description="Query system status and metrics.",
                risk_tier=ToolRiskTier.SYSTEM,
                required_capability="system.health",
                rate_limit_per_minute=60,
            ),
        )
    )

"""Unit tests for fixed, permissioned tool registry (§26, §27).

Verifies:
- ToolSpec schema, rate limit, and validation constraints.
- Risk level classifications and high-risk audit enforcement.
- Immutable ToolRegistry lookups and duplicate rejection.
- authorize() enforcement: permission gating and caller type checks.
- Unknown tool rejection fails closed.
- Default registry completeness and security posture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.commerce_repository import (
    InMemoryInventoryRepository,
    InMemoryProductRepository,
)
from agentpay.adapters.in_memory.identity_repository import (
    InMemoryAgentRepository,
    InMemoryCredentialRepository,
)
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.application.use_cases.purchase_flow import PurchaseOrchestrator
from agentpay.domain.commerce import Inventory, Product
from agentpay.domain.errors import (
    AgentRevokedError,
    InvalidCommerceError,
    InvalidIdentityError,
    LoopBudgetExceededError,
    ToolCallerNotAllowedError,
    ToolPermissionDeniedError,
    UnauthorizedToolAccessError,
    UnknownToolError,
)
from agentpay.domain.identity import Agent, AgentStatus
from agentpay.domain.identity_token import PrincipalType
from agentpay.domain.safety import AgentLoopBudget, AgentLoopTracker
from agentpay.domain.tool_registry import (
    ToolAuditBehavior,
    ToolDefinition,
    ToolRegistry,
    ToolRiskLevel,
    ToolRiskTier,
    ToolSpec,
    build_default_tool_registry,
)


def test_tool_spec_validation() -> None:
    """ToolSpec validates non-empty strings and valid schema dictionaries."""
    # Valid spec
    spec = ToolSpec(
        name="test_tool",
        description="A test tool",
        input_schema={"q": "str"},
        output_schema={"res": "list"},
        required_permission="test.perm",
        risk_level=ToolRiskLevel.READ,
        rate_limit_per_minute=60,
    )
    assert spec.name == "test_tool"
    assert spec.risk_level == ToolRiskLevel.READ
    assert not spec.is_high_risk
    assert spec.effective_audit_behavior == ToolAuditBehavior.LOG

    # Empty name rejected
    with pytest.raises(InvalidCommerceError):
        ToolSpec(
            name="",
            description="desc",
            input_schema={},
            output_schema={},
            required_permission="perm",
            risk_level=ToolRiskLevel.READ,
            rate_limit_per_minute=60,
        )

    # Negative rate limit rejected
    with pytest.raises(InvalidCommerceError):
        ToolSpec(
            name="tool",
            description="desc",
            input_schema={},
            output_schema={},
            required_permission="perm",
            risk_level=ToolRiskLevel.READ,
            rate_limit_per_minute=-1,
        )

    # Boolean rate limit rejected
    with pytest.raises(InvalidCommerceError):
        ToolSpec(
            name="tool",
            description="desc",
            input_schema={},
            output_schema={},
            required_permission="perm",
            risk_level=ToolRiskLevel.READ,
            rate_limit_per_minute=True,
        )


def test_tool_risk_classification_and_audit_behavior() -> None:
    """High risk tools automatically default to ALWAYS audit and is_high_risk=True."""
    financial_spec = ToolSpec(
        name="charge_money",
        description="Transfers money",
        input_schema={},
        output_schema={},
        required_permission="payment.charge",
        risk_level=ToolRiskLevel.FINANCIAL,
        rate_limit_per_minute=10,
    )
    assert financial_spec.is_high_risk
    assert financial_spec.effective_audit_behavior == ToolAuditBehavior.ALWAYS
    assert financial_spec.requires_audit

    destructive_spec = ToolSpec(
        name="delete_account",
        description="Destroys account data",
        input_schema={},
        output_schema={},
        required_permission="admin.delete",
        risk_level=ToolRiskLevel.DESTRUCTIVE,
        rate_limit_per_minute=1,
    )
    assert destructive_spec.is_high_risk
    assert destructive_spec.effective_audit_behavior == ToolAuditBehavior.ALWAYS


def test_tool_registry_duplicate_rejection() -> None:
    """ToolRegistry rejects duplicate tool names at initialization."""
    spec1 = ToolSpec(
        name="tool_a",
        description="first",
        input_schema={},
        output_schema={},
        required_permission="p",
        risk_level=ToolRiskLevel.READ,
        rate_limit_per_minute=60,
    )
    spec2 = ToolSpec(
        name="tool_a",
        description="duplicate",
        input_schema={},
        output_schema={},
        required_permission="p",
        risk_level=ToolRiskLevel.READ,
        rate_limit_per_minute=60,
    )
    with pytest.raises(InvalidCommerceError, match="duplicate tool registration"):
        ToolRegistry((spec1, spec2))


def test_tool_registry_lookups() -> None:
    """ToolRegistry provides exact lookups and risk filtering."""
    read_spec = ToolSpec(
        name="search",
        description="search",
        input_schema={},
        output_schema={},
        required_permission="p.read",
        risk_level=ToolRiskLevel.READ,
        rate_limit_per_minute=60,
    )
    fin_spec = ToolSpec(
        name="pay",
        description="pay",
        input_schema={},
        output_schema={},
        required_permission="p.pay",
        risk_level=ToolRiskLevel.FINANCIAL,
        rate_limit_per_minute=10,
    )
    registry = ToolRegistry((read_spec, fin_spec))

    assert registry.has("search")
    assert registry.has("pay")
    assert not registry.has("unknown_tool")

    assert registry.get("search") == read_spec
    with pytest.raises(UnknownToolError):
        registry.get("unknown_tool")

    assert registry.names_for_risk(ToolRiskLevel.READ) == ("search",)
    assert registry.names_for_risk(ToolRiskLevel.FINANCIAL) == ("pay",)


def test_tool_authorization_permission_denied() -> None:
    """authorize() raises ToolPermissionDeniedError if caller lacks required permission."""
    spec = ToolSpec(
        name="restricted_tool",
        description="Restricted",
        input_schema={},
        output_schema={},
        required_permission="restricted.scope",
        risk_level=ToolRiskLevel.WRITE_MEDIUM,
        rate_limit_per_minute=30,
    )
    registry = ToolRegistry((spec,))

    # Missing permission
    with pytest.raises(ToolPermissionDeniedError, match=r"requires permission 'restricted\.scope'"):
        registry.authorize(
            name="restricted_tool",
            granted_permissions=frozenset({"other.scope"}),
            caller_type=PrincipalType.AGENT,
        )

    # Permission granted -> success
    authorized = registry.authorize(
        name="restricted_tool",
        granted_permissions=frozenset({"restricted.scope", "other.scope"}),
        caller_type=PrincipalType.AGENT,
    )
    assert authorized == spec


def test_tool_authorization_caller_type_denied() -> None:
    """authorize() raises ToolCallerNotAllowedError if principal type is not allowed."""
    human_only_spec = ToolSpec(
        name="human_override",
        description="Only human operators may call",
        input_schema={},
        output_schema={},
        required_permission="override.grant",
        risk_level=ToolRiskLevel.SECURITY_SENSITIVE,
        rate_limit_per_minute=5,
        allowed_callers=frozenset({PrincipalType.HUMAN}),
    )
    registry = ToolRegistry((human_only_spec,))

    # Agent caller rejected
    with pytest.raises(ToolCallerNotAllowedError, match="may not be called by a AGENT principal"):
        registry.authorize(
            name="human_override",
            granted_permissions=frozenset({"override.grant"}),
            caller_type=PrincipalType.AGENT,
        )

    # Human caller permitted
    authorized = registry.authorize(
        name="human_override",
        granted_permissions=frozenset({"override.grant"}),
        caller_type=PrincipalType.HUMAN,
    )
    assert authorized == human_only_spec


def test_default_tool_registry_contains_canonical_tools() -> None:
    """build_default_tool_registry() contains expected commerce tools with appropriate risk tiers."""
    reg = build_default_tool_registry()
    assert reg.has("search_products")
    assert reg.has("get_product")
    assert reg.has("compare_products")
    assert reg.has("get_evidence")
    assert reg.has("check_inventory")
    assert reg.has("request_payment")

    # Financial tools are high-risk
    purchase_tool = reg.get("request_payment")
    assert purchase_tool.is_high_risk
    assert purchase_tool.risk_level == ToolRiskLevel.FINANCIAL
    assert purchase_tool.required_permission == "payment.request"


def make_test_agent(
    agent_id: str = "agent_01",
    tenant_id: str = "tenant_01",
    capabilities: tuple[str, ...] = ("catalog.search", "catalog.read", "payment.request"),
    status: AgentStatus = AgentStatus.ACTIVE,
) -> Agent:
    now = datetime.now(UTC)
    return Agent(
        agent_id=agent_id,
        tenant_id=tenant_id,
        principal_id="user_01",
        issuer="https://auth.agentpay.test",
        capabilities=capabilities,
        scopes=("commerce:read", "commerce:write"),
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        status=status,
    )


def test_tool_risk_tier_enum() -> None:
    """Verify all 4 canonical risk tiers defined in §26, §27."""
    assert ToolRiskTier.READ_ONLY == "READ_ONLY"
    assert ToolRiskTier.FINANCIAL_MUTATING == "FINANCIAL_MUTATING"
    assert ToolRiskTier.ADMINISTRATIVE == "ADMINISTRATIVE"
    assert ToolRiskTier.SYSTEM == "SYSTEM"


def test_tool_definition_validation() -> None:
    """ToolDefinition validates fields and enforces fail-closed invariants (§26, §27)."""
    tool = ToolDefinition(
        name="test_tool",
        description="A test tool",
        risk_tier=ToolRiskTier.READ_ONLY,
        required_capability="test.capability",
    )
    assert tool.name == "test_tool"
    assert tool.enabled is True
    assert tool.rate_limit_per_minute == 60
    assert tool.risk_tier == ToolRiskTier.READ_ONLY
    assert not tool.is_high_risk
    assert tool.required_permission == "test.capability"
    assert tool.risk_level == ToolRiskLevel.READ

    financial_tool = ToolDefinition(
        name="pay_tool",
        description="A financial tool",
        risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
        required_capability="payment.request",
        enabled=False,
        rate_limit_per_minute=10,
    )
    assert financial_tool.is_high_risk
    assert financial_tool.enabled is False
    assert financial_tool.risk_level == ToolRiskLevel.FINANCIAL

    admin_tool = ToolDefinition(
        name="admin_tool",
        description="An admin tool",
        risk_tier=ToolRiskTier.ADMINISTRATIVE,
        required_capability="agent.revoke",
    )
    assert admin_tool.is_high_risk
    assert admin_tool.risk_level == ToolRiskLevel.SECURITY_SENSITIVE

    # Empty name rejected
    with pytest.raises(InvalidCommerceError, match="tool name must be a non-empty string"):
        ToolDefinition(
            name="",
            description="desc",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="cap",
        )

    # Empty description rejected
    with pytest.raises(InvalidCommerceError, match="tool description must be a non-empty string"):
        ToolDefinition(
            name="name",
            description="",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="cap",
        )

    # Empty required_capability rejected
    with pytest.raises(
        InvalidCommerceError, match="tool required_capability must be a non-empty string"
    ):
        ToolDefinition(
            name="name",
            description="desc",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="   ",
        )

    # Invalid risk tier rejected
    with pytest.raises(InvalidCommerceError, match="invalid tool risk_tier"):
        ToolDefinition(
            name="name",
            description="desc",
            risk_tier="INVALID",  # type: ignore[arg-type]
            required_capability="cap",
        )

    # Non-boolean enabled rejected
    with pytest.raises(InvalidCommerceError, match="tool enabled must be a boolean"):
        ToolDefinition(
            name="name",
            description="desc",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="cap",
            enabled="true",  # type: ignore[arg-type]
        )

    # Non-positive rate limit rejected
    with pytest.raises(
        InvalidCommerceError, match="rate_limit_per_minute must be a positive integer"
    ):
        ToolDefinition(
            name="name",
            description="desc",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="cap",
            rate_limit_per_minute=0,
        )

    # Boolean rate limit rejected
    with pytest.raises(
        InvalidCommerceError, match="rate_limit_per_minute must be a positive integer"
    ):
        ToolDefinition(
            name="name",
            description="desc",
            risk_tier=ToolRiskTier.READ_ONLY,
            required_capability="cap",
            rate_limit_per_minute=True,
        )


def test_tool_registry_with_tool_definitions() -> None:
    """ToolRegistry manages ToolDefinition instances with risk filtering and duplicate rejection."""
    t1 = ToolDefinition(
        name="search",
        description="search",
        risk_tier=ToolRiskTier.READ_ONLY,
        required_capability="catalog.search",
    )
    t2 = ToolDefinition(
        name="order",
        description="order",
        risk_tier=ToolRiskTier.FINANCIAL_MUTATING,
        required_capability="order.create",
    )
    reg = ToolRegistry((t1, t2))

    assert reg.has("search")
    assert reg.has("order")
    assert not reg.has("unknown")

    assert reg.get("search") == t1
    assert reg.get("order") == t2

    assert reg.names_for_risk(ToolRiskTier.READ_ONLY) == ("search",)
    assert reg.names_for_risk(ToolRiskTier.FINANCIAL_MUTATING) == ("order",)

    # Duplicate rejected
    with pytest.raises(InvalidCommerceError, match="duplicate tool registration"):
        ToolRegistry((t1, t1))


def test_tool_registry_authorize_agent() -> None:
    """ToolRegistry.authorize(name, agent) fails closed on missing capability, disabled tool, or unknown tool."""
    enabled_tool = ToolDefinition(
        name="view_catalog",
        description="View catalog",
        risk_tier=ToolRiskTier.READ_ONLY,
        required_capability="catalog.read",
        enabled=True,
    )
    disabled_tool = ToolDefinition(
        name="dangerous_tool",
        description="Disabled tool",
        risk_tier=ToolRiskTier.ADMINISTRATIVE,
        required_capability="system.admin",
        enabled=False,
    )
    reg = ToolRegistry((enabled_tool, disabled_tool))

    agent_with_cap = make_test_agent(capabilities=("catalog.read", "system.admin"))
    agent_without_cap = make_test_agent(capabilities=("other.cap",))

    # Unknown tool raises UnknownToolError
    with pytest.raises(UnknownToolError, match="Tool 'missing' is not registered"):
        reg.authorize("missing", agent_with_cap)

    # Disabled tool raises UnauthorizedToolAccessError
    with pytest.raises(UnauthorizedToolAccessError, match="Tool 'dangerous_tool' is disabled"):
        reg.authorize("dangerous_tool", agent_with_cap)

    # Missing capability raises UnauthorizedToolAccessError
    with pytest.raises(
        UnauthorizedToolAccessError, match=r"lacks required capability 'catalog\.read'"
    ):
        reg.authorize("view_catalog", agent_without_cap)

    # Granted capability succeeds
    authorized = reg.authorize("view_catalog", agent_with_cap)
    assert authorized == enabled_tool


def test_agent_authorization_service_tool_call() -> None:
    """AgentAuthorizationService.authorize_tool_call checks agent lifecycle and capability (§15, §26)."""
    auth_service = AgentAuthorizationService()
    active_agent = make_test_agent(capabilities=("catalog.search",))
    revoked_agent = make_test_agent(capabilities=("catalog.search",), status=AgentStatus.REVOKED)

    # Revoked agent fails closed
    with pytest.raises(AgentRevokedError):
        auth_service.authorize_tool_call(revoked_agent, "search_products")

    # Unknown tool fails closed
    with pytest.raises(UnknownToolError):
        auth_service.authorize_tool_call(active_agent, "unknown_tool")

    # Missing capability fails closed
    with pytest.raises(UnauthorizedToolAccessError):
        auth_service.authorize_tool_call(active_agent, "request_payment")

    # Active agent with capability succeeds
    tool_def = auth_service.authorize_tool_call(active_agent, "search_products")
    assert tool_def.name == "search_products"
    assert tool_def.risk_tier == ToolRiskTier.READ_ONLY


def test_purchase_orchestrator_execute_tool_success_and_failure() -> None:
    """PurchaseOrchestrator.execute_tool validates tenant boundaries, authorization, and loop budget (§26, §64)."""
    tenant_id = "tenant_01"
    agent_repo = InMemoryAgentRepository()
    product_repo = InMemoryProductRepository()
    inventory_repo = InMemoryInventoryRepository()
    audit_ledger = InMemoryAuditLedger()

    agent = make_test_agent(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "catalog.read", "inventory.read", "system.health"),
    )
    agent_repo.save(tenant_id, agent)

    product = Product(
        product_id="prod_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Mechanical Keyboard",
        category="electronics",
        description="A fine keyboard",
    )
    product_repo.save(tenant_id, product)

    inventory = Inventory(
        variant_id="var_01",
        tenant_id=tenant_id,
        available=42,
        reserved=2,
    )
    inventory_repo.save(tenant_id, inventory)

    orchestrator = PurchaseOrchestrator(
        agent_repo=agent_repo,
        product_repo=product_repo,
        inventory_repo=inventory_repo,
        audit_ledger=audit_ledger,
    )

    # 1. Non-existent agent in tenant fails closed
    with pytest.raises(InvalidIdentityError, match="not found in tenant"):
        orchestrator.execute_tool(tenant_id, "unknown_agent", "search_products")

    # 2. Unknown tool fails closed
    with pytest.raises(UnknownToolError):
        orchestrator.execute_tool(tenant_id, "agent_01", "non_existent_tool")

    # 3. Missing capability fails closed
    with pytest.raises(UnauthorizedToolAccessError):
        orchestrator.execute_tool(tenant_id, "agent_01", "request_payment")

    # 4. Successful tool calls
    res_search = orchestrator.execute_tool(
        tenant_id, "agent_01", "search_products", {"query": "Mechanical"}
    )
    assert res_search["status"] == "success"
    assert res_search["tool_name"] == "search_products"
    assert res_search["risk_tier"] == "READ_ONLY"
    assert len(res_search["result"]["products"]) == 1

    res_product = orchestrator.execute_tool(
        tenant_id, "agent_01", "get_product", {"product_id": "prod_01"}
    )
    assert res_product["status"] == "success"
    assert res_product["result"]["product"]["name"] == "Mechanical Keyboard"

    res_inv = orchestrator.execute_tool(
        tenant_id, "agent_01", "check_inventory", {"variant_id": "var_01"}
    )
    assert res_inv["status"] == "success"
    assert res_inv["result"]["available"] == 42

    res_health = orchestrator.execute_tool(tenant_id, "agent_01", "system_health")
    assert res_health["status"] == "success"
    assert res_health["risk_tier"] == "SYSTEM"

    # Audit ledger has recorded events
    events = audit_ledger.get_events(tenant_id)
    assert len(events) >= 4

    # Loop budget protection halts excessive iterations (§64)
    budget_orchestrator = PurchaseOrchestrator(
        agent_repo=agent_repo,
    )
    # Configure tiny loop budget
    tracker = budget_orchestrator._loop_trackers.setdefault(
        f"{tenant_id}:agent_01", AgentLoopTracker(budget=AgentLoopBudget(max_tool_calls=2))
    )
    tracker.record_tool_call()
    tracker.record_tool_call()
    with pytest.raises(LoopBudgetExceededError, match="Tool call limit 2 exceeded"):
        budget_orchestrator.execute_tool(tenant_id, "agent_01", "system_health")


def test_api_tool_execution_endpoint_success_and_errors() -> None:
    """AgentPayApiApp tool endpoints enforce authentication, tenant verification, and error envelopes (§13, §14, §26)."""
    tenant_id = "tenant_01"
    agent_repo = InMemoryAgentRepository()
    product_repo = InMemoryProductRepository()
    credential_repo = InMemoryCredentialRepository()

    agent = make_test_agent(
        agent_id="agent_01",
        tenant_id=tenant_id,
        capabilities=("catalog.search", "catalog.read"),
    )
    agent_repo.save(tenant_id, agent)

    product = Product(
        product_id="prod_01",
        tenant_id=tenant_id,
        merchant_id="merch_01",
        name="Ultra Monitor",
        category="electronics",
        description="A fine monitor",
    )
    product_repo.save(tenant_id, product)

    orchestrator = PurchaseOrchestrator(
        agent_repo=agent_repo,
        product_repo=product_repo,
    )
    app = AgentPayApiApp(orchestrator=orchestrator, credential_repo=credential_repo)
    client = ApiTestClient(app, default_tenant=tenant_id)

    # 1. Unauthenticated invocation rejected with HTTP 401 UNAUTHENTICATED
    status, body = app.handle_request(
        "POST",
        "/api/v1/tools/search_products/invoke",
        {"X-Tenant-ID": tenant_id},
        {"agent_id": "agent_01"},
    )
    assert status == 401
    assert body["error"]["code"] == "UNAUTHENTICATED"

    # Create authorized credential
    valid_token = client.create_token(tenant_id=tenant_id, principal_id="agent_01")
    auth_headers = {
        "Authorization": f"Bearer {valid_token}",
        "X-Tenant-ID": tenant_id,
    }

    # 2. Cross-tenant request rejected with HTTP 403 CROSS_TENANT_ACCESS_DENIED
    status, body = app.handle_request(
        "POST",
        "/api/v1/tools/search_products/invoke",
        {"Authorization": f"Bearer {valid_token}", "X-Tenant-ID": "tenant_other"},
        {"agent_id": "agent_01"},
    )
    assert status == 403
    assert body["error"]["code"] == "CROSS_TENANT_ACCESS_DENIED"

    # 3. Unknown tool rejected with HTTP 400 UNKNOWN_TOOL
    status, body = app.handle_request(
        "POST",
        "/api/v1/tools/unregistered_tool/invoke",
        auth_headers,
        {"agent_id": "agent_01"},
    )
    assert status == 400
    assert body["error"]["code"] == "UNKNOWN_TOOL"

    # 4. Unauthorized capability call rejected with HTTP 403 UNAUTHORIZED_TOOL_ACCESS
    status, body = app.handle_request(
        "POST",
        "/api/v1/tools/request_payment/invoke",
        auth_headers,
        {"agent_id": "agent_01"},
    )
    assert status == 403
    assert body["error"]["code"] == "UNAUTHORIZED_TOOL_ACCESS"

    # 5. Permitted tool call succeeds on /api/v1/tools/{name}/invoke
    status, body = app.handle_request(
        "POST",
        "/api/v1/tools/search_products/invoke",
        auth_headers,
        {"agent_id": "agent_01", "parameters": {"query": "Monitor"}},
    )
    assert status == 200
    assert body["status"] == "success"
    assert body["tool_name"] == "search_products"
    assert len(body["result"]["products"]) == 1

    # 6. Permitted tool call succeeds on /agent/tools/{name}/execute
    status, body = app.handle_request(
        "POST",
        "/agent/tools/get_product/execute",
        auth_headers,
        {"agent_id": "agent_01", "parameters": {"product_id": "prod_01"}},
    )
    assert status == 200
    assert body["status"] == "success"
    assert body["tool_name"] == "get_product"
    assert body["result"]["product"]["name"] == "Ultra Monitor"

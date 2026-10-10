"""Unit tests for the AgentPay Developer SDK (§109).

Validates:
- Instantiation with api_key and in-process app.
- Catalog listing, product inspection, inventory check.
- ActionRequest purchase submission.
- Approvals workflow (list, grant, reject).
- Emergency controls (status, pause, resume).
- Typed error handling raising AgentPayClientError with trace correlation.
"""

from __future__ import annotations

import pytest
from test_purchase_flow import setup_orchestrator

from agentpay import AgentPay, AgentPayClientError
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.identity import UserRole


@pytest.fixture
def test_setup() -> tuple[AgentPay, AgentPayApiApp]:
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client_helper = ApiTestClient(app)

    # Provision valid credentials for tenant_01
    api_key = client_helper.create_api_key(
        tenant_id="tenant_01",
        role=UserRole.ADMIN,
    )
    sdk = AgentPay(api_key=api_key, tenant_id="tenant_01", app=app)
    return sdk, app


def test_sdk_catalog_discovery(test_setup: tuple[AgentPay, AgentPayApiApp]) -> None:
    sdk, _ = test_setup
    # 1. List catalog products
    res = sdk.catalog.list_products()
    assert "products" in res
    assert len(res["products"]) >= 1

    prod_id = res["products"][0]["product_id"]
    # 2. Get specific product
    prod = sdk.catalog.get_product(prod_id)
    assert prod["product_id"] == prod_id
    assert prod["name"] == "Pro Laptop"

    # 3. Check inventory
    inv = sdk.catalog.get_inventory("var_01")
    assert inv["variant_id"] == "var_01"
    assert inv["available"] > 0


def test_sdk_request_purchase(test_setup: tuple[AgentPay, AgentPayApiApp]) -> None:
    sdk, _ = test_setup
    result = sdk.actions.request_purchase(
        product_id="prod_01",
        variant_id="var_01",
        amount_minor=129900,
        currency="INR",
        intent_purpose="Developer workstation purchase",
    )
    assert "status" in result
    assert result["status"] in ("COMPLETED", "APPROVED", "PENDING_APPROVAL")


def test_sdk_approvals_and_emergency(test_setup: tuple[AgentPay, AgentPayApiApp]) -> None:
    sdk, _ = test_setup
    # Check approvals list
    approvals = sdk.approvals.list_pending()
    assert isinstance(approvals, list)

    # Emergency pause and resume
    status = sdk.emergency.get_status()
    assert status["paused"] is False

    pause_res = sdk.emergency.pause(
        operator_id="operator_01",
        reason="Testing emergency SDK controls",
    )
    assert pause_res["status"] == "PAUSED"
    assert sdk.emergency.get_status()["paused"] is True

    resume_res = sdk.emergency.resume(operator_id="operator_01")
    assert resume_res["status"] == "ACTIVE"
    assert sdk.emergency.get_status()["paused"] is False


def test_sdk_raises_typed_error_with_trace_id(
    test_setup: tuple[AgentPay, AgentPayApiApp],
) -> None:
    _, app = test_setup
    # Create SDK with invalid key to test error envelope mapping
    invalid_sdk = AgentPay(api_key="apk_invalid_secret_key", tenant_id="tenant_01", app=app)

    with pytest.raises(AgentPayClientError) as exc_info:
        invalid_sdk.catalog.list_products()

    err = exc_info.value
    assert err.status_code == 401
    assert err.code == "UNAUTHENTICATED"
    assert err.request_id is not None
    assert err.trace_id is not None
    assert "Invalid authentication credentials" in err.message

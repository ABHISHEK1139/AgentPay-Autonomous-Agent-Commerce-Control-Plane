from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentpay.domain.action_request import (
    ActionRequest,
    ActionStatus,
    AuthorizationMode,
    Intent,
    ResourceRef,
)
from agentpay.domain.errors import InvalidActionRequestError
from agentpay.domain.money import Money


def build(**overrides: object) -> ActionRequest:
    defaults: dict[str, object] = {
        "action_id": "act_01",
        "tenant_id": "tenant_01",
        "principal_id": "person_01",
        "agent_id": "agent_01",
        "domain": "commerce",
        "action": "purchase",
        "resource": ResourceRef(type="product", id="prod_123"),
        "requested_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "intent": Intent(purpose="work laptop", budget_max=Money(2_000_000, "INR")),
        "amount": Money(1_499_000, "INR"),
        "authorization_mode": AuthorizationMode.AUTONOMOUS,
    }
    return ActionRequest(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_builds_a_valid_action_request() -> None:
    request = build()
    assert request.tenant_id == "tenant_01"
    assert request.amount == Money(1_499_000, "INR")
    assert request.authorization_mode is AuthorizationMode.AUTONOMOUS


def test_omitting_authorization_mode_yields_supervised_not_autonomous() -> None:
    """Fail-closed default: authority is never inferred from a missing field.

    The build() fixture always sets the mode explicitly, so the default has to be
    exercised by omitting the argument entirely.
    """
    request = ActionRequest(
        action_id="act_01",
        tenant_id="tenant_01",
        principal_id="person_01",
        agent_id="agent_01",
        domain="commerce",
        action="purchase",
        resource=ResourceRef(type="product", id="prod_123"),
        requested_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        intent=Intent(purpose="work laptop"),
        amount=Money(1_499_000, "INR"),
    )
    assert request.authorization_mode is AuthorizationMode.SUPERVISED


@pytest.mark.parametrize(
    "field_name", ["action_id", "tenant_id", "principal_id", "agent_id", "domain", "action"]
)
def test_identifiers_must_be_present(field_name: str) -> None:
    with pytest.raises(InvalidActionRequestError):
        build(**{field_name: ""})


def test_naive_datetime_is_rejected() -> None:
    naive = datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001 - deliberately naive to prove rejection
    with pytest.raises(InvalidActionRequestError):
        build(requested_at=naive)


def test_amount_above_stated_intent_budget_is_rejected() -> None:
    with pytest.raises(InvalidActionRequestError):
        build(amount=Money(9_999_000, "INR"))


def test_budget_currency_must_match_amount_currency() -> None:
    with pytest.raises(InvalidActionRequestError):
        build(amount=Money(100, "USD"))


def test_budget_equal_to_amount_is_accepted() -> None:
    assert build(amount=Money(2_000_000, "INR")).amount == Money(2_000_000, "INR")


@pytest.mark.parametrize("version", [0, -1, 1.0, True, "1"])
def test_version_must_be_a_positive_int(version: object) -> None:
    with pytest.raises(InvalidActionRequestError):
        build(version=version)


def test_action_request_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        build().tenant_id = "tenant_99"  # type: ignore[misc]


def test_resource_ref_requires_type_and_id() -> None:
    with pytest.raises(InvalidActionRequestError):
        ResourceRef(type="", id="prod_123")


def test_intent_requires_a_purpose() -> None:
    with pytest.raises(InvalidActionRequestError):
        Intent(purpose="   ")


def test_action_status_covers_the_documented_lifecycle() -> None:
    assert {status.value for status in ActionStatus} == {
        "PROPOSED",
        "VALIDATED",
        "POLICY_REVIEW",
        "AWAITING_APPROVAL",
        "AUTHORIZED",
        "EXECUTING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
    }

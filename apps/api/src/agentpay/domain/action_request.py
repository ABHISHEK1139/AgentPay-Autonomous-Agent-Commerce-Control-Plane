from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from agentpay.domain.errors import InvalidActionRequestError
from agentpay.domain.money import Money


class AuthorizationMode(StrEnum):
    """How much authority the requester claims. Policy decides what is actually granted."""

    READ_ONLY = "READ_ONLY"
    ASSISTED = "ASSISTED"
    SUPERVISED = "SUPERVISED"
    AUTONOMOUS = "AUTONOMOUS"


class ActionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    POLICY_REVIEW = "POLICY_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    type: str
    id: str

    def __post_init__(self) -> None:
        fields: tuple[tuple[str, object], ...] = (("type", self.type), ("id", self.id))
        for field_name, value in fields:
            if not isinstance(value, str) or not value.strip():
                raise InvalidActionRequestError(f"resource {field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Intent:
    purpose: str
    budget_max: Money | None = None

    def __post_init__(self) -> None:
        purpose: object = self.purpose
        if not isinstance(purpose, str) or not purpose.strip():
            raise InvalidActionRequestError("intent.purpose must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """The canonical abstraction every domain-specific operation is normalised into."""

    action_id: str
    tenant_id: str
    principal_id: str
    agent_id: str
    domain: str
    action: str
    resource: ResourceRef
    requested_at: datetime
    intent: Intent
    amount: Money | None = None
    authorization_mode: AuthorizationMode = AuthorizationMode.SUPERVISED
    version: int = 1

    def __post_init__(self) -> None:
        identifiers: tuple[tuple[str, object], ...] = tuple(
            (name, getattr(self, name))
            for name in ("action_id", "tenant_id", "principal_id", "agent_id", "domain", "action")
        )
        for field_name, value in identifiers:
            if not isinstance(value, str) or not value.strip():
                raise InvalidActionRequestError(f"{field_name} must be a non-empty string")

        if not isinstance(self.resource, ResourceRef):
            raise InvalidActionRequestError("resource must be a ResourceRef")
        if not isinstance(self.intent, Intent):
            raise InvalidActionRequestError("intent must be an Intent")

        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise InvalidActionRequestError("requested_at must be timezone-aware (UTC)")

        version: object = self.version
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise InvalidActionRequestError(f"version must be a positive int, got {version!r}")

        budget_max = self.intent.budget_max
        if self.amount is not None and budget_max is not None:
            if budget_max.currency != self.amount.currency:
                raise InvalidActionRequestError(
                    f"intent budget {budget_max.currency} does not match amount "
                    f"{self.amount.currency}"
                )
            if not self.amount.is_at_most(budget_max):
                raise InvalidActionRequestError(
                    f"requested amount {self.amount} exceeds stated intent budget {budget_max}"
                )

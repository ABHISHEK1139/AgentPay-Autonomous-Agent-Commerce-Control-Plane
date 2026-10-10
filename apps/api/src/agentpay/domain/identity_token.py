"""Authenticated principal and access-token claims (§14, §15, §17).

These are pure domain value objects. They carry no cryptography and perform no I/O:
signing and verification belong to an adapter, validation of the resulting claims
belongs here. The security-critical property enforced by the platform is that a
caller's identity comes from a verified token, never from a self-asserted header.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from agentpay.domain.errors import (
    AgentIdentityMismatchError,
    AuthenticationError,
    AuthorizationError,
    MissingScopeError,
    TokenAudienceError,
    TokenExpiredError,
    TokenRevokedError,
)


class PrincipalType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SERVICE = "SERVICE"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified claims extracted from an access token (§14).

    ``tenant_id`` inside the token is the authority for tenant scope. A caller-supplied
    tenant header is only ever compared against this value; it never establishes it.
    """

    principal_id: str
    tenant_id: str
    principal_type: PrincipalType
    scopes: frozenset[str]
    audience: str
    issued_at: datetime
    expires_at: datetime
    jti: str
    agent_id: str | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        strings: tuple[tuple[str, object], ...] = (
            ("principal_id", self.principal_id),
            ("tenant_id", self.tenant_id),
            ("audience", self.audience),
            ("jti", self.jti),
        )
        for name, val in strings:
            val_obj: object = val
            if not isinstance(val_obj, str) or not val_obj.strip():
                raise AuthenticationError(f"token claim {name} must be a non-empty string")

        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise AuthenticationError("issued_at must be timezone-aware (UTC)")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise AuthenticationError("expires_at must be timezone-aware (UTC)")
        if self.expires_at <= self.issued_at:
            raise AuthenticationError("token must expire after it was issued")

        if self.principal_type is PrincipalType.AGENT and not self.agent_id:
            raise AuthenticationError("AGENT tokens must carry agent_id")

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now(UTC)

    def requires_expired_check(self, now: datetime) -> bool:
        return self.expires_at <= now

    def assert_valid(self, now: datetime, expected_audience: str) -> None:
        """Fail closed on expiry, revocation, or audience mismatch (§14)."""
        if self.revoked:
            raise TokenRevokedError("token has been revoked")
        if self.requires_expired_check(now):
            raise TokenExpiredError("token has expired")
        if self.audience != expected_audience:
            raise TokenAudienceError("token audience mismatch")


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """The authenticated caller for a single request (§14).

    Created only from verified claims. The tenant is fixed here, so every downstream
    call is scoped by construction rather than by trusting request input.
    """

    principal_id: str
    tenant_id: str
    principal_type: PrincipalType
    scopes: frozenset[str]
    agent_id: str | None = None

    def assert_tenant_matches(self, asserted_tenant_id: str) -> None:
        """Reject any request whose tenant header contradicts the token (§49, §50)."""
        if asserted_tenant_id and asserted_tenant_id != self.tenant_id:
            raise AuthorizationError(
                "requested tenant does not match the authenticated principal's tenant"
            )

    def assert_scope(self, required_scope: str) -> None:
        if required_scope not in self.scopes:
            raise MissingScopeError(f"missing required scope: {required_scope}")

    def assert_agent_matches(self, claimed_agent_id: str) -> None:
        """An agent token may act only as the agent it was issued for (§15, §16)."""
        if self.principal_type is not PrincipalType.AGENT:
            return
        if claimed_agent_id != self.agent_id:
            raise AgentIdentityMismatchError("agent token cannot act as a different agent")


@dataclass(frozen=True, slots=True)
class TokenIssuancePolicy:
    """Short token lifetimes are a requirement, not a default (§14)."""

    human_max_lifetime: timedelta = timedelta(minutes=15)
    agent_max_lifetime: timedelta = timedelta(minutes=5)
    service_max_lifetime: timedelta = timedelta(minutes=30)
    audience: str = "agentpay-api"

    def max_lifetime_for(self, principal_type: PrincipalType) -> timedelta:
        if principal_type is PrincipalType.HUMAN:
            return self.human_max_lifetime
        if principal_type is PrincipalType.AGENT:
            return self.agent_max_lifetime
        return self.service_max_lifetime

    def assert_lifetime_allowed(self, lifetime: timedelta, principal_type: PrincipalType) -> None:
        if lifetime <= timedelta(0):
            raise AuthenticationError("token lifetime must be positive")
        if lifetime > self.max_lifetime_for(principal_type):
            raise AuthenticationError(
                f"token lifetime exceeds the maximum allowed for {principal_type.value} principals"
            )

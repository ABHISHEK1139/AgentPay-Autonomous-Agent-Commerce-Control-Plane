"""Application ports for identity, tenancy, and agent authorization persistence.

Following Clean Architecture (§8, §10), these ports define abstract protocols
for repository access. Every tenant-owned entity repository strictly requires
an explicit `tenant_id` partition parameter (§49, §50).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Tenant,
)


@runtime_checkable
class TenantRepository(Protocol):
    """Port for tenant root partition persistence and lookup."""

    def get(self, tenant_id: str) -> Tenant | None:
        """Retrieve a tenant by unique identifier. Returns None if not found."""
        ...

    def save(self, tenant: Tenant) -> None:
        """Persist or update a tenant entity."""
        ...

    def exists(self, tenant_id: str) -> bool:
        """Check if a tenant exists."""
        ...

    def list(self) -> list[Tenant]:
        """List all tenants."""
        ...


@runtime_checkable
class AgentRepository(Protocol):
    """Port for agent entity persistence and lifecycle management.

    CRITICAL SECURITY INVARIANT (§49, §50):
    Every repository method requires an explicit `tenant_id` partition parameter.
    Cross-tenant queries are blocked at the port interface.
    """

    def get(self, tenant_id: str, agent_id: str) -> Agent | None:
        """Retrieve an agent within a specific tenant partition.

        Returns None if the agent does not exist in the specified tenant partition.
        """
        ...

    def save(self, tenant_id: str, agent: Agent) -> None:
        """Persist or update an agent within a tenant partition.

        Must raise CrossTenantAccessError if agent.tenant_id != tenant_id.
        """
        ...

    def list(self, tenant_id: str) -> list[Agent]:
        """List all agents within a specific tenant partition."""
        ...

    def update_status(self, tenant_id: str, agent_id: str, status: AgentStatus) -> None:
        """Update the lifecycle status of an agent within a tenant partition.

        Must fail closed (raise IdentityError or AgentRevokedError) if the
        agent does not exist or is already REVOKED.
        """
        ...


@runtime_checkable
class AgentPermissionsRepository(Protocol):
    """Port for agent permissions and spending limits persistence."""

    def get(self, tenant_id: str, agent_id: str) -> AgentPermissions | None:
        """Retrieve permissions configured for an agent in a tenant partition."""
        ...

    def save(self, tenant_id: str, permissions: AgentPermissions) -> None:
        """Persist or update permissions for an agent in a tenant partition.

        Must raise CrossTenantAccessError if permissions.tenant_id != tenant_id.
        """
        ...

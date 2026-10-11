"""Thread-safe, strictly partitioned in-memory identity repositories.

Enforces:
- Explicit multi-tenant data partitioning (§49, §50).
- Thread-safe access via re-entrant locks.
- Standardized tenant-scoped cache key naming (§51).
- Terminal lifecycle state immutability for REVOKED agents.
"""

from __future__ import annotations

import threading

from agentpay.application.ports.identity_port import (
    AgentPermissionsRepository,
    AgentRepository,
    CredentialRepository,
    TenantRepository,
)
from agentpay.domain.errors import (
    AgentRevokedError,
    CrossTenantAccessError,
    InvalidIdentityError,
)
from agentpay.domain.identity import (
    Agent,
    AgentPermissions,
    AgentStatus,
    Credential,
    Tenant,
)


def format_tenant_cache_key(tenant_id: str, resource_type: str, resource_id: str) -> str:
    """Format a cache key strictly scoped to a tenant boundary (§51).

    Pattern: tenant:{tenant_id}:{resource_type}:{resource_id}

    Examples:
        tenant:tenant_01:agent:agent_01
        tenant:tenant_01:permissions:agent_01
        tenant:tenant_01:product:prod_123
    """
    segments: tuple[tuple[str, object], ...] = (
        ("tenant_id", tenant_id),
        ("resource_type", resource_type),
        ("resource_id", resource_id),
    )
    for name, raw_val in segments:
        if not isinstance(raw_val, str) or not raw_val.strip():
            raise ValueError(f"{name} cannot be empty")
        if ":" in raw_val:
            raise ValueError("Colons are not permitted in cache key segments")
        if any(c in raw_val for c in ("\r", "\n", "\0", "\x00")):
            raise ValueError(
                f"Control characters (CR, LF, null byte) are not permitted in cache key segment '{name}'"
            )

    return f"tenant:{tenant_id}:{resource_type}:{resource_id}"


# Alias for backward and forward specification compatibility
format_cache_key = format_tenant_cache_key


class InMemoryTenantRepository(TenantRepository):
    """Thread-safe in-memory repository for Tenant entities."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tenants: dict[str, Tenant] = {}

    def get(self, tenant_id: str) -> Tenant | None:
        with self._lock:
            return self._tenants.get(tenant_id)

    def save(self, tenant: Tenant) -> None:
        with self._lock:
            self._tenants[tenant.tenant_id] = tenant

    def exists(self, tenant_id: str) -> bool:
        with self._lock:
            return tenant_id in self._tenants

    def list(self) -> list[Tenant]:
        with self._lock:
            return list(self._tenants.values())

    def clear(self) -> None:
        """Reset repository state for testing."""
        with self._lock:
            self._tenants.clear()


class InMemoryAgentRepository(AgentRepository):
    """Thread-safe, multi-tenant partitioned repository for Agents.

    Storage is partitioned by tenant_id as the primary key:
        self._agents: {tenant_id: {agent_id: Agent}}
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, dict[str, Agent]] = {}
        self._permissions: dict[str, dict[str, AgentPermissions]] = {}

    def get(self, tenant_id: str, agent_id: str) -> Agent | None:
        with self._lock:
            tenant_partition = self._agents.get(tenant_id)
            if tenant_partition is None:
                return None
            return tenant_partition.get(agent_id)

    def save(self, tenant_id: str, agent: Agent) -> None:
        """Persist or update an agent in the tenant partition.

        Raises:
            CrossTenantAccessError: If agent.tenant_id != tenant_id (§50).
        """
        if agent.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"tenant_id mismatch: agent.tenant_id '{agent.tenant_id}' "
                f"does not match repository partition tenant_id '{tenant_id}'"
            )

        with self._lock:
            if tenant_id not in self._agents:
                self._agents[tenant_id] = {}
            existing_agent = self._agents[tenant_id].get(agent.agent_id)
            if existing_agent is not None and existing_agent.status is AgentStatus.REVOKED:
                raise AgentRevokedError(
                    f"Cannot overwrite or resurrect terminal REVOKED agent '{agent.agent_id}'"
                )
            self._agents[tenant_id][agent.agent_id] = agent

    def list(self, tenant_id: str) -> list[Agent]:
        with self._lock:
            tenant_partition = self._agents.get(tenant_id)
            if tenant_partition is None:
                return []
            return list(tenant_partition.values())

    def update_status(self, tenant_id: str, agent_id: str, status: AgentStatus) -> None:
        """Update an agent's lifecycle status.

        Raises:
            InvalidIdentityError: If agent does not exist in partition.
            AgentRevokedError: If existing agent is already REVOKED (terminal state).
        """
        with self._lock:
            tenant_partition = self._agents.get(tenant_id)
            if tenant_partition is None or agent_id not in tenant_partition:
                raise InvalidIdentityError(
                    f"Agent '{agent_id}' does not exist in tenant partition '{tenant_id}'"
                )

            existing_agent = tenant_partition[agent_id]
            if existing_agent.status is AgentStatus.REVOKED:
                raise AgentRevokedError(
                    f"Cannot transition from terminal REVOKED agent '{agent_id}'"
                )

            updated_agent = existing_agent.transition_to(status)
            tenant_partition[agent_id] = updated_agent

    def get_permissions(self, tenant_id: str, agent_id: str) -> AgentPermissions | None:
        with self._lock:
            tenant_partition = self._permissions.get(tenant_id)
            if tenant_partition is None:
                return None
            return tenant_partition.get(agent_id)

    def save_permissions(self, tenant_id: str, permissions: AgentPermissions) -> None:
        if permissions.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Permissions tenant_id '{permissions.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._lock:
            if tenant_id not in self._permissions:
                self._permissions[tenant_id] = {}
            self._permissions[tenant_id][permissions.agent_id] = permissions

    def clear(self) -> None:
        """Reset repository state for testing."""
        with self._lock:
            self._agents.clear()
            self._permissions.clear()


class InMemoryAgentPermissionsRepository(AgentPermissionsRepository):
    """Thread-safe, multi-tenant partitioned repository for AgentPermissions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._permissions: dict[str, dict[str, AgentPermissions]] = {}

    def get(self, tenant_id: str, agent_id: str) -> AgentPermissions | None:
        with self._lock:
            tenant_partition = self._permissions.get(tenant_id)
            if tenant_partition is None:
                return None
            return tenant_partition.get(agent_id)

    def save(self, tenant_id: str, permissions: AgentPermissions) -> None:
        if permissions.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Permissions tenant_id '{permissions.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._lock:
            if tenant_id not in self._permissions:
                self._permissions[tenant_id] = {}
            self._permissions[tenant_id][permissions.agent_id] = permissions

    def clear(self) -> None:
        """Reset repository state for testing."""
        with self._lock:
            self._permissions.clear()


class InMemoryCredentialRepository(CredentialRepository):
    """Thread-safe, multi-tenant partitioned repository for Credentials (§14)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._credentials: dict[str, dict[str, Credential]] = {}
        self._by_hash: dict[str, Credential] = {}

    def get(self, tenant_id: str, credential_id: str) -> Credential | None:
        with self._lock:
            tenant_partition = self._credentials.get(tenant_id)
            if tenant_partition is None:
                return None
            return tenant_partition.get(credential_id)

    def get_by_secret_hash(self, secret_hash: str) -> Credential | None:
        with self._lock:
            return self._by_hash.get(secret_hash)

    def save(self, tenant_id: str, credential: Credential) -> None:
        if credential.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Credential tenant_id '{credential.tenant_id}' does not match partition '{tenant_id}'"
            )
        with self._lock:
            if tenant_id not in self._credentials:
                self._credentials[tenant_id] = {}
            self._credentials[tenant_id][credential.credential_id] = credential
            self._by_hash[credential.secret_hash] = credential

    def revoke(self, tenant_id: str, credential_id: str) -> None:
        with self._lock:
            cred = self.get(tenant_id, credential_id)
            if cred is not None:
                from agentpay.domain.identity import CredentialStatus

                revoked = Credential(
                    credential_id=cred.credential_id,
                    tenant_id=cred.tenant_id,
                    principal_id=cred.principal_id,
                    credential_type=cred.credential_type,
                    secret_hash=cred.secret_hash,
                    expires_at=cred.expires_at,
                    status=CredentialStatus.REVOKED,
                    role=cred.role,
                    scopes=cred.scopes,
                )
                self._credentials[tenant_id][credential_id] = revoked
                self._by_hash[cred.secret_hash] = revoked

    def clear(self) -> None:
        """Reset repository state for testing."""
        with self._lock:
            self._credentials.clear()
            self._by_hash.clear()

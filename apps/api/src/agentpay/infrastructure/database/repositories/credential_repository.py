"""SQLAlchemy implementation of CredentialRepository port (§14, §49, §50)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.identity_port import CredentialRepository
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.identity import Credential, CredentialStatus
from agentpay.infrastructure.database.models.credential import CredentialModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyCredentialRepository(BaseSqlAlchemyRepository, CredentialRepository):
    """SQLAlchemy-backed repository for Credential persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, credential_id: str) -> Credential | None:
        with self._session() as session:
            stmt = select(CredentialModel).where(
                CredentialModel.tenant_id == tenant_id,
                CredentialModel.credential_id == credential_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def get_by_secret_hash(self, secret_hash: str) -> Credential | None:
        with self._session() as session:
            stmt = select(CredentialModel).where(CredentialModel.secret_hash == secret_hash)
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(
        self, tenant_id_or_cred: str | Credential, credential: Credential | None = None
    ) -> None:
        """Persist or update a credential within a tenant partition.

        Supports both save(credential) and save(tenant_id, credential).
        """
        if isinstance(tenant_id_or_cred, Credential):
            target_cred = tenant_id_or_cred
            tenant_id = target_cred.tenant_id
        else:
            tenant_id = tenant_id_or_cred
            if credential is None:
                raise ValueError("Credential entity must be provided")
            target_cred = credential

        if target_cred.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Credential tenant '{target_cred.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(CredentialModel).where(
                CredentialModel.tenant_id == tenant_id,
                CredentialModel.credential_id == target_cred.credential_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(CredentialModel.from_domain(target_cred))
            else:
                existing.principal_id = target_cred.principal_id
                existing.credential_type = target_cred.credential_type.value
                existing.secret_hash = target_cred.secret_hash
                existing.status = target_cred.status.value
                existing.role = target_cred.role.value if target_cred.role else None
                existing.scopes = list(target_cred.scopes)
                existing.expires_at = target_cred.expires_at
            session.commit()

    def revoke(self, tenant_id: str, credential_id: str) -> None:
        with self._session() as session:
            stmt = select(CredentialModel).where(
                CredentialModel.tenant_id == tenant_id,
                CredentialModel.credential_id == credential_id,
            )
            existing = session.scalar(stmt)
            if existing is not None:
                existing.status = CredentialStatus.REVOKED.value
                session.commit()

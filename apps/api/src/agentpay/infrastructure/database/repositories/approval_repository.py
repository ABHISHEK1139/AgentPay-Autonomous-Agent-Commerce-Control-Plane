"""SQLAlchemy implementation of ApprovalRepository port (§20, §49, §50)."""

from __future__ import annotations

import threading
from datetime import UTC

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.approval_port import ApprovalRepository
from agentpay.domain.approval import (
    Approval,
    ApprovalAlreadyConsumedError,
    ApprovalBinding,
    ApprovalStatus,
)
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    InvalidCommerceError,
)
from agentpay.domain.money import Money
from agentpay.infrastructure.database.models.approval import ApprovalModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyApprovalRepository(BaseSqlAlchemyRepository, ApprovalRepository):
    """SQLAlchemy-backed repository for Approval entities with exact 7-field binding."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)
        self._lock = threading.Lock()

    def get(self, tenant_id: str, approval_id: str) -> Approval | None:
        with self._session() as session:
            stmt = select(ApprovalModel).where(
                ApprovalModel.tenant_id == tenant_id,
                ApprovalModel.approval_id == approval_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return self._to_domain(model)

    def save(self, tenant_id: str, approval: Approval) -> None:
        if approval.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Approval tenant '{approval.tenant_id}' does not match context '{tenant_id}'"
            )

        with self._session() as session:
            model = session.get(ApprovalModel, approval.approval_id)
            if model is None:
                model = ApprovalModel(
                    approval_id=approval.approval_id,
                    tenant_id=approval.tenant_id,
                    action_id=approval.binding.action_id,
                    amount_minor=approval.binding.amount.minor_units,
                    currency=approval.binding.currency,
                    merchant_id=approval.binding.merchant_id,
                    resource_id=approval.binding.resource_id,
                    action_version=approval.binding.action_version,
                    status=approval.status.value,
                    requested_at=approval.requested_at,
                    approver_id=approval.approver_id,
                    decided_at=approval.decided_at,
                    reason=approval.reason,
                )
                session.add(model)
            else:
                if model.tenant_id != tenant_id:
                    raise CrossTenantAccessError(
                        f"Existing approval tenant '{model.tenant_id}' does not match partition '{tenant_id}'"
                    )
                model.status = approval.status.value
                model.approver_id = approval.approver_id
                model.decided_at = approval.decided_at
                model.reason = approval.reason
            session.commit()

    def list_for_action(self, tenant_id: str, action_id: str) -> list[Approval]:
        with self._session() as session:
            stmt = (
                select(ApprovalModel)
                .where(
                    ApprovalModel.tenant_id == tenant_id,
                    ApprovalModel.action_id == action_id,
                )
                .order_by(ApprovalModel.requested_at)
            )
            models = session.scalars(stmt).all()
            return [self._to_domain(m) for m in models]

    def atomic_consume(self, tenant_id: str, approval_id: str) -> Approval:
        """Atomically verify and transition approval to CONSUMED under transaction (§20, §177 Vector 10)."""
        with self._lock, self._session() as session:
            stmt = (
                update(ApprovalModel)
                .where(
                    ApprovalModel.tenant_id == tenant_id,
                    ApprovalModel.approval_id == approval_id,
                    ApprovalModel.status == ApprovalStatus.APPROVED.value,
                )
                .values(status=ApprovalStatus.CONSUMED.value)
            )
            res = session.execute(stmt)
            session.commit()
            rowcount = res.rowcount if isinstance(res, CursorResult) else 0
            if rowcount == 0:
                # Inspect why update matched 0 rows:
                check_stmt = select(ApprovalModel).where(
                    ApprovalModel.tenant_id == tenant_id,
                    ApprovalModel.approval_id == approval_id,
                )
                model = session.scalar(check_stmt)
                if model is None:
                    raise InvalidCommerceError(
                        f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
                    )
                if model.status == ApprovalStatus.CONSUMED.value:
                    raise ApprovalAlreadyConsumedError(
                        f"Approval '{approval_id}' has already been consumed"
                    )
                raise ApprovalBindingMismatchError(
                    f"Approval is in status '{model.status}', not APPROVED"
                )
            # Retrieve consumed model to convert to domain entity:
            get_stmt = select(ApprovalModel).where(
                ApprovalModel.tenant_id == tenant_id,
                ApprovalModel.approval_id == approval_id,
            )
            consumed_model = session.scalar(get_stmt)
            if consumed_model is None:
                raise InvalidCommerceError(
                    f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
                )
            return self._to_domain(consumed_model)

    def _to_domain(self, model: ApprovalModel) -> Approval:
        req_at = model.requested_at
        if req_at.tzinfo is None:
            req_at = req_at.replace(tzinfo=UTC)
        dec_at = model.decided_at
        if dec_at is not None and dec_at.tzinfo is None:
            dec_at = dec_at.replace(tzinfo=UTC)

        binding = ApprovalBinding(
            action_id=model.action_id,
            amount=Money(model.amount_minor, model.currency),
            merchant_id=model.merchant_id,
            resource_id=model.resource_id,
            currency=model.currency,
            action_version=model.action_version,
            tenant_id=model.tenant_id,
        )
        return Approval(
            approval_id=model.approval_id,
            tenant_id=model.tenant_id,
            binding=binding,
            status=ApprovalStatus(model.status),
            requested_at=req_at,
            approver_id=model.approver_id,
            decided_at=dec_at,
            reason=model.reason,
        )

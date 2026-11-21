"""SQLAlchemy implementation of CartRepository port (§38, §49, §50, ADR-0006)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.commerce_port import CartRepository
from agentpay.domain.cart import Cart, CartStatus
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.infrastructure.database.models.cart import CartItemModel, CartModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyCartRepository(BaseSqlAlchemyRepository, CartRepository):
    """SQLAlchemy-backed repository for Cart persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, cart_id: str) -> Cart | None:
        with self._session() as session:
            stmt = select(CartModel).where(
                CartModel.tenant_id == tenant_id,
                CartModel.cart_id == cart_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id: str, cart: Cart) -> None:
        if cart.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Cart tenant '{cart.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(CartModel).where(
                CartModel.tenant_id == tenant_id,
                CartModel.cart_id == cart.cart_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(CartModel.from_domain(cart))
            else:
                existing.principal_id = cart.principal_id
                existing.agent_id = cart.agent_id
                existing.merchant_id = cart.merchant_id
                existing.status = cart.status.value
                existing.updated_at = cart.updated_at
                session.query(CartItemModel).filter(CartItemModel.cart_id == cart.cart_id).delete(
                    synchronize_session=False
                )
                session.add_all(
                    [
                        CartItemModel(
                            cart_id=cart.cart_id,
                            position=position,
                            variant_id=item.variant_id,
                            quantity=item.quantity,
                        )
                        for position, item in enumerate(cart.items)
                    ]
                )
            session.commit()

    def list_for_principal(self, tenant_id: str, principal_id: str) -> list[Cart]:
        with self._session() as session:
            stmt = (
                select(CartModel)
                .where(
                    CartModel.tenant_id == tenant_id,
                    CartModel.principal_id == principal_id,
                )
                .order_by(CartModel.created_at)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

    def mark_processing(self, tenant_id: str, cart_id: str) -> bool:
        with self._session() as session:
            stmt = (
                update(CartModel)
                .where(
                    CartModel.tenant_id == tenant_id,
                    CartModel.cart_id == cart_id,
                    CartModel.status == CartStatus.ACTIVE.value,
                )
                .values(
                    status=CartStatus.PROCESSING.value,
                    updated_at=datetime.now(UTC),
                )
            )
            result = session.execute(stmt)
            session.commit()
            rowcount = result.rowcount if isinstance(result, CursorResult) else 0
            return rowcount > 0

    def mark_active(self, tenant_id: str, cart_id: str) -> bool:
        with self._session() as session:
            stmt = (
                update(CartModel)
                .where(
                    CartModel.tenant_id == tenant_id,
                    CartModel.cart_id == cart_id,
                    CartModel.status == CartStatus.PROCESSING.value,
                )
                .values(
                    status=CartStatus.ACTIVE.value,
                    updated_at=datetime.now(UTC),
                )
            )
            result = session.execute(stmt)
            session.commit()
            rowcount = result.rowcount if isinstance(result, CursorResult) else 0
            return rowcount > 0

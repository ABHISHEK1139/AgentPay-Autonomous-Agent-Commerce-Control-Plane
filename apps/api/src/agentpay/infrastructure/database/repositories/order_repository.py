"""SQLAlchemy implementation of OrderRepository port (§41, §44, §49, §50)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.payment_port import OrderRepository
from agentpay.domain.errors import CrossTenantAccessError
from agentpay.domain.payments import Order
from agentpay.infrastructure.database.models.order import OrderModel
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyOrderRepository(BaseSqlAlchemyRepository, OrderRepository):
    """SQLAlchemy-backed repository for Order persistence with strict tenant scoping."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, tenant_id: str, order_id: str) -> Order | None:
        with self._session() as session:
            stmt = select(OrderModel).where(
                OrderModel.tenant_id == tenant_id,
                OrderModel.order_id == order_id,
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def find_by_payment_attempt(self, tenant_id: str, attempt_id: str) -> Order | None:
        with self._session() as session:
            stmt = select(OrderModel).where(
                OrderModel.tenant_id == tenant_id,
                (OrderModel.payment_attempt_id == attempt_id) | (OrderModel.order_id == attempt_id),
            )
            model = session.scalar(stmt)
            if model is None:
                return None
            return model.to_domain()

    def save(self, tenant_id_or_order: str | Order, order: Order | None = None) -> None:
        """Persist or update an order entity.

        Supports both save(order) and save(tenant_id, order).
        """
        if isinstance(tenant_id_or_order, Order):
            target_order = tenant_id_or_order
            tenant_id = target_order.tenant_id
        else:
            tenant_id = tenant_id_or_order
            if order is None:
                raise ValueError("Order entity must be provided")
            target_order = order

        if target_order.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Order tenant '{target_order.tenant_id}' does not match partition '{tenant_id}'"
            )

        with self._session() as session:
            stmt = select(OrderModel).where(
                OrderModel.tenant_id == tenant_id,
                OrderModel.order_id == target_order.order_id,
            )
            existing = session.scalar(stmt)
            if existing is None:
                session.add(OrderModel.from_domain(target_order))
            else:
                existing.merchant_id = target_order.merchant_id
                existing.buyer_id = target_order.buyer_id
                existing.agent_id = target_order.agent_id
                existing.variant_id = target_order.variant_id
                existing.quantity = target_order.quantity
                existing.total_amount_minor = target_order.total_amount.minor_units
                existing.total_amount_currency = target_order.total_amount.currency
                existing.status = target_order.status.value
                existing.payment_attempt_id = target_order.payment_attempt_id
            session.commit()

    def list_for_buyer(self, tenant_id: str, buyer_id: str) -> list[Order]:
        with self._session() as session:
            stmt = (
                select(OrderModel)
                .where(
                    OrderModel.tenant_id == tenant_id,
                    OrderModel.buyer_id == buyer_id,
                )
                .order_by(OrderModel.created_at)
            )
            models = session.scalars(stmt).all()
            return [m.to_domain() for m in models]

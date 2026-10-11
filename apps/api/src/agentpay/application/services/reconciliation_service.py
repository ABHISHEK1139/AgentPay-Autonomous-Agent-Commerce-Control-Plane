"""Payment timeout reconciliation service (§43).

On timeout the platform must query the provider for the authoritative status instead of
blindly retrying a charge. This service encapsulates exactly that: it performs a read-only
provider query for attempts stuck in UNKNOWN and persists the outcome. It never creates,
captures, or refunds — reconciliation observes, it does not move money.
"""

from __future__ import annotations

import uuid
from typing import Any

from agentpay.application.ports.audit_port import AuditLedger
from agentpay.application.ports.commerce_port import InventoryRepository
from agentpay.application.ports.payment_port import (
    OrderRepository,
    PaymentProvider,
    PaymentRepository,
)
from agentpay.domain.audit import AuditEventType
from agentpay.domain.errors import CrossTenantAccessError, InvalidPaymentError
from agentpay.domain.payments import (
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    Refund,
)


class PaymentReconciler:
    """Resolve UNKNOWN payment attempts by querying the provider (§43) and ingest webhooks (§61)."""

    def __init__(
        self,
        payment_provider: PaymentProvider,
        payment_repo: PaymentRepository,
        order_repo: OrderRepository | None = None,
        inventory_repo: InventoryRepository | None = None,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._provider = payment_provider
        self._repo = payment_repo
        self._payment_repo = payment_repo
        self._order_repo = order_repo
        self._inventory_repo = inventory_repo
        self._audit_ledger = audit_ledger

    def reconcile_unknown_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        """Return the authoritative status of an attempt, querying the provider only if UNKNOWN."""
        stored = self._repo.get(tenant_id, attempt_id)
        if stored is None:
            raise InvalidPaymentError(
                f"Payment attempt '{attempt_id}' not found in tenant '{tenant_id}'"
            )
        if stored.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Payment attempt tenant '{stored.tenant_id}' does not match context '{tenant_id}'"
            )
        if stored.status is not PaymentStatus.UNKNOWN:
            return stored

        refreshed = self._provider.get_payment(tenant_id, attempt_id)
        if refreshed.attempt_id != attempt_id or refreshed.tenant_id != tenant_id:
            raise InvalidPaymentError(
                f"Provider returned a mismatched attempt for '{attempt_id}' in tenant '{tenant_id}'"
            )
        self._repo.save(tenant_id, refreshed)
        return refreshed

    def reconcile_timeout(self, attempt: PaymentAttempt) -> PaymentAttempt:
        """Resolve a timeout attempt by querying the provider for BackgroundWorker compatibility (§43, §104)."""
        return self.reconcile_unknown_payment(attempt.tenant_id, attempt.attempt_id)

    def reconcile_webhook_event(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        trace_id: str = "",
    ) -> dict[str, Any]:
        """Reconcile asynchronous payment events from external provider webhooks (§61).

        Supports:
        - payment.captured: confirms payment attempt & associated order, logs audit.
        - payment.failed: marks attempt failed, cancels order, compensation-releases inventory, logs audit.
        - payment.refunded: marks attempt refunded, creates Refund, compensation-restocks inventory, logs audit.
        """
        raw_data = payload.get("data")
        data_dict: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        attempt_id = str(
            payload.get("attempt_id")
            or payload.get("payment_id")
            or data_dict.get("attempt_id")
            or data_dict.get("payment_id")
            or ""
        ).strip()

        if not attempt_id:
            raise InvalidPaymentError("Missing attempt_id or payment_id in webhook payload")

        attempt = self._repo.get(tenant_id, attempt_id)
        if attempt is None:
            raise InvalidPaymentError(
                f"Payment attempt '{attempt_id}' not found in tenant '{tenant_id}'"
            )
        if attempt.tenant_id != tenant_id:
            raise CrossTenantAccessError(
                f"Payment attempt tenant '{attempt.tenant_id}' does not match context '{tenant_id}'"
            )

        provider_payment_id = str(
            payload.get("provider_payment_id")
            or data_dict.get("provider_payment_id")
            or payload.get("id")
            or attempt.provider_payment_id
            or f"prov_{attempt_id}"
        )
        failure_reason = str(
            payload.get("reason")
            or payload.get("failure_reason")
            or data_dict.get("reason")
            or "Payment failed via webhook notification"
        )

        # Locate associated Order if order_repo configured
        order: Order | None = None
        if self._order_repo is not None:
            order_id = str(
                payload.get("order_id") or data_dict.get("order_id") or attempt.intent_id or ""
            ).strip()
            if order_id:
                order = self._order_repo.get(tenant_id, order_id)
            if order is None and hasattr(self._order_repo, "_orders"):
                for candidate in self._order_repo._orders.get(tenant_id, {}).values():
                    if (
                        candidate.payment_attempt_id == attempt_id
                        or candidate.order_id == attempt_id
                    ):
                        order = candidate
                        break

        refund: Refund | None = None

        if event_type == "payment.captured":
            if attempt.status in (PaymentStatus.FAILED, PaymentStatus.DECLINED):
                raise InvalidPaymentError(
                    f"Cannot capture payment '{attempt_id}' in terminal failure status '{attempt.status.value}'"
                )
            if attempt.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
                raise InvalidPaymentError(
                    f"Cannot capture payment '{attempt_id}' in status '{attempt.status.value}'"
                )

            if attempt.status is PaymentStatus.CAPTURED:
                if (
                    order is not None
                    and self._order_repo is not None
                    and order.status is OrderStatus.PENDING_PAYMENT
                ):
                    confirmed_order = order.confirm(payment_attempt_id=attempt_id)
                    self._order_repo.save(tenant_id, confirmed_order)
                    order = confirmed_order
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": None,
                }

            updated_attempt = attempt.transition_to(
                PaymentStatus.CAPTURED,
                provider_payment_id=provider_payment_id,
            )
            self._repo.save(tenant_id, updated_attempt)

            if (
                order is not None
                and self._order_repo is not None
                and order.status is OrderStatus.PENDING_PAYMENT
            ):
                confirmed_order = order.confirm(payment_attempt_id=attempt_id)
                self._order_repo.save(tenant_id, confirmed_order)
                order = confirmed_order

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_CAPTURED,
                    action_id=attempt.intent_id,
                    tenant_id=tenant_id,
                    actor="provider_webhook",
                    data={
                        "attempt_id": attempt_id,
                        "provider_payment_id": provider_payment_id,
                        "order_id": order.order_id if order else None,
                        "status": PaymentStatus.CAPTURED.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=trace_id,
                )

        elif event_type == "payment.failed":
            if attempt.status is PaymentStatus.CAPTURED:
                raise InvalidPaymentError(
                    f"Cannot fail payment '{attempt_id}' in status 'CAPTURED'"
                )
            if attempt.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
                raise InvalidPaymentError(
                    f"Cannot fail payment '{attempt_id}' in status '{attempt.status.value}'"
                )

            if attempt.status in (PaymentStatus.FAILED, PaymentStatus.DECLINED):
                if (
                    order is not None
                    and self._order_repo is not None
                    and order.status is OrderStatus.PENDING_PAYMENT
                ):
                    cancelled_order = order.cancel(reason=failure_reason)
                    self._order_repo.save(tenant_id, cancelled_order)
                    order = cancelled_order
                    if self._inventory_repo is not None:
                        self._inventory_repo.release(tenant_id, order.variant_id, order.quantity)
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": None,
                }

            updated_attempt = attempt.transition_to(
                PaymentStatus.FAILED,
                failure_reason=failure_reason,
            )
            self._repo.save(tenant_id, updated_attempt)

            if order is not None and self._order_repo is not None:
                if order.status is OrderStatus.PENDING_PAYMENT:
                    cancelled_order = order.cancel(reason=failure_reason)
                    self._order_repo.save(tenant_id, cancelled_order)
                    order = cancelled_order

                    # SAGA compensation: release reserved inventory
                    if self._inventory_repo is not None:
                        self._inventory_repo.release(tenant_id, order.variant_id, order.quantity)
            elif self._inventory_repo is not None:
                variant_id = str(payload.get("variant_id") or data_dict.get("variant_id") or "")
                if variant_id:
                    qty = int(payload.get("quantity") or data_dict.get("quantity") or 1)
                    self._inventory_repo.release(tenant_id, variant_id, qty)

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_FAILED,
                    action_id=attempt.intent_id,
                    tenant_id=tenant_id,
                    actor="provider_webhook",
                    data={
                        "attempt_id": attempt_id,
                        "failure_reason": failure_reason,
                        "order_id": order.order_id if order else None,
                        "status": PaymentStatus.FAILED.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=trace_id,
                )
                if order is not None and order.status is OrderStatus.CANCELLED:
                    self._audit_ledger.record_event(
                        event_type=AuditEventType.ORDER_CANCELLED,
                        action_id=attempt.intent_id,
                        tenant_id=tenant_id,
                        actor="provider_webhook",
                        data={
                            "order_id": order.order_id,
                            "reason": failure_reason,
                        },
                        agent_id=order.agent_id,
                        trace_id=trace_id,
                    )

        elif event_type == "payment.refunded":
            if (
                attempt.status is not PaymentStatus.CAPTURED
                and attempt.status is not PaymentStatus.REFUNDED
            ):
                raise InvalidPaymentError(
                    f"Cannot refund payment '{attempt_id}' in status '{attempt.status.value}'; only CAPTURED payments can be refunded"
                )

            if attempt.status is PaymentStatus.REFUNDED:
                existing_refund_id = payload.get("refund_id") or data_dict.get("refund_id")
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": str(existing_refund_id) if existing_refund_id else None,
                }

            updated_attempt = attempt.transition_to(PaymentStatus.REFUNDED)
            self._repo.save(tenant_id, updated_attempt)

            refund_id = str(
                payload.get("refund_id")
                or data_dict.get("refund_id")
                or f"ref_{uuid.uuid4().hex[:12]}"
            )
            refund_reason = str(
                payload.get("reason") or data_dict.get("reason") or "Payment refunded via webhook"
            )
            refund = Refund(
                refund_id=refund_id,
                payment_attempt_id=attempt_id,
                tenant_id=tenant_id,
                amount=attempt.amount,
                reason=refund_reason,
            )

            # SAGA restock if applicable
            if order is not None and self._order_repo is not None:
                if order.status is OrderStatus.CONFIRMED:
                    refunded_order = Order(
                        order_id=order.order_id,
                        tenant_id=order.tenant_id,
                        merchant_id=order.merchant_id,
                        buyer_id=order.buyer_id,
                        agent_id=order.agent_id,
                        variant_id=order.variant_id,
                        quantity=order.quantity,
                        total_amount=order.total_amount,
                        status=OrderStatus.REFUNDED,
                        payment_attempt_id=order.payment_attempt_id,
                        created_at=order.created_at,
                    )
                    self._order_repo.save(tenant_id, refunded_order)
                    order = refunded_order

                    if self._inventory_repo is not None:
                        self._inventory_repo.release(tenant_id, order.variant_id, order.quantity)
            elif self._inventory_repo is not None:
                variant_id = str(payload.get("variant_id") or data_dict.get("variant_id") or "")
                if variant_id:
                    qty = int(payload.get("quantity") or data_dict.get("quantity") or 1)
                    self._inventory_repo.release(tenant_id, variant_id, qty)

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_REFUNDED,
                    action_id=attempt.intent_id,
                    tenant_id=tenant_id,
                    actor="provider_webhook",
                    data={
                        "attempt_id": attempt_id,
                        "refund_id": refund.refund_id,
                        "amount_minor": refund.amount.minor_units,
                        "reason": refund.reason,
                        "order_id": order.order_id if order else None,
                        "status": PaymentStatus.REFUNDED.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=trace_id,
                )

        else:
            raise InvalidPaymentError(f"Unsupported webhook event type '{event_type}'")

        return {
            "status": "PROCESSED",
            "event_type": event_type,
            "attempt_id": attempt_id,
            "payment_status": updated_attempt.status.value,
            "order_id": order.order_id if order else None,
            "order_status": order.status.value if order else None,
            "refund_id": refund.refund_id if refund else None,
        }

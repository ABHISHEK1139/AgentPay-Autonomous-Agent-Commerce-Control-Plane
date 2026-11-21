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
    ProviderTransactionMappingRepository,
    ProviderWebhookEventRepository,
    RefundRepository,
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
        provider_mapping_repo: ProviderTransactionMappingRepository | None = None,
        webhook_event_repo: ProviderWebhookEventRepository | None = None,
        refund_repo: RefundRepository | None = None,
    ) -> None:
        self._provider = payment_provider
        self._repo = payment_repo
        self._payment_repo = payment_repo
        self._order_repo = order_repo
        self._inventory_repo = inventory_repo
        self._audit_ledger = audit_ledger
        self._provider_mapping_repo = provider_mapping_repo
        self._webhook_event_repo = webhook_event_repo
        self._refund_repo = refund_repo

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

        # Prefer a provider-driven timeout reconciliation when the provider offers
        # one (e.g. the mock provider resolves simulated timeouts to CAPTURED);
        # otherwise fall back to querying the provider for the current status.
        provider_reconcile = getattr(self._provider, "reconcile_timeout", None)
        refreshed: PaymentAttempt
        if callable(provider_reconcile):
            refreshed = provider_reconcile(tenant_id, attempt_id)
        else:
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
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile asynchronous payment events from external provider webhooks (§61).

        Supports:
        - payment.captured: confirms payment attempt & associated order, logs audit.
        - payment.failed: marks attempt failed, cancels order, compensation-releases inventory, logs audit.
        - payment.refunded: marks attempt refunded, creates Refund, compensation-restocks inventory, logs audit.
        """
        effective_trace_id = trace_id.strip() if (trace_id and trace_id.strip()) else None
        raw_data = payload.get("data")
        data_dict: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        provider = str(payload.get("provider") or "").strip().lower()
        if not provider and self._provider is not None:
            cname = type(self._provider).__name__.lower()
            if "mock" in cname:
                provider = "mock"
            elif "razorpay" in cname:
                provider = "razorpay"
            elif "stripe" in cname:
                provider = "stripe"

        # Candidate transaction IDs
        prov_tx_id = str(
            payload.get("provider_payment_id")
            or data_dict.get("provider_payment_id")
            or payload.get("id")
            or data_dict.get("id")
            or ""
        ).strip()
        candidate_attempt_id = str(
            payload.get("attempt_id")
            or payload.get("payment_id")
            or data_dict.get("attempt_id")
            or data_dict.get("payment_id")
            or ""
        ).strip()
        provider_account_id = (
            str(payload.get("account_id") or data_dict.get("account_id") or "").strip() or None
        )

        effective_tenant = tenant_id.strip() if tenant_id else ""
        effective_attempt_id = candidate_attempt_id

        # 1. Resolve tenant & attempt strictly from stored mapping (§39, §41, §49)
        mapping = None
        if self._provider_mapping_repo is not None:
            if prov_tx_id:
                mapping = self._provider_mapping_repo.find_by_provider_transaction(
                    provider=provider,
                    provider_transaction_id=prov_tx_id,
                    provider_account_id=provider_account_id,
                )
            if mapping is None and candidate_attempt_id:
                mapping = self._provider_mapping_repo.find_by_provider_transaction(
                    provider=provider,
                    provider_transaction_id=candidate_attempt_id,
                    provider_account_id=provider_account_id,
                )
            if mapping is None and effective_tenant and candidate_attempt_id:
                mapping = self._provider_mapping_repo.find_by_attempt_id(
                    tenant_id=effective_tenant,
                    attempt_id=candidate_attempt_id,
                )

        if mapping is not None:
            if effective_tenant and effective_tenant != mapping.tenant_id:
                raise CrossTenantAccessError(
                    f"Caller asserted tenant '{effective_tenant}' does not match mapped tenant '{mapping.tenant_id}'"
                )
            effective_tenant = mapping.tenant_id
            effective_attempt_id = mapping.attempt_id
        elif not effective_tenant:
            # Unmapped & unauthenticated: fail-closed quarantine (§41, §49, AP-001)
            # Emit security alert under "SYSTEM" without mutating orders/inventory
            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.SECURITY_ALERT,
                    action_id=f"quarantine_{uuid.uuid4().hex[:12]}",
                    tenant_id="SYSTEM",
                    actor="webhook_quarantine",
                    data={
                        "reason": "UNRESOLVED_WEBHOOK_TRANSACTION",
                        "provider": provider,
                        "provider_payment_id": prov_tx_id or candidate_attempt_id,
                        "event_type": event_type,
                    },
                    trace_id=effective_trace_id,
                )
            return {
                "status": "QUARANTINED",
                "reason": "UNRESOLVED_WEBHOOK_TRANSACTION",
                "provider": provider,
                "provider_payment_id": prov_tx_id or candidate_attempt_id,
            }

        if not effective_attempt_id:
            raise InvalidPaymentError("Missing attempt_id or payment_id in webhook payload")

        attempt = self._repo.get(effective_tenant, effective_attempt_id)
        if attempt is None:
            raise InvalidPaymentError(
                f"Payment attempt '{effective_attempt_id}' not found in tenant '{effective_tenant}'"
            )
        if attempt.tenant_id != effective_tenant:
            raise CrossTenantAccessError(
                f"Payment attempt tenant '{attempt.tenant_id}' does not match context '{effective_tenant}'"
            )

        provider_payment_id = str(
            prov_tx_id or attempt.provider_payment_id or f"prov_{effective_attempt_id}"
        )
        failure_reason = str(
            payload.get("reason")
            or payload.get("failure_reason")
            or data_dict.get("reason")
            or "Payment failed via webhook notification"
        )

        # Locate associated Orders if order_repo configured
        orders: list[Order] = []
        if self._order_repo is not None:
            if hasattr(self._order_repo, "find_all_by_payment_attempt"):
                orders = self._order_repo.find_all_by_payment_attempt(
                    effective_tenant, effective_attempt_id
                )
            if not orders:
                order_id = str(
                    payload.get("order_id") or data_dict.get("order_id") or attempt.intent_id or ""
                ).strip()
                if order_id:
                    single = self._order_repo.get(effective_tenant, order_id)
                    if single is not None:
                        orders = [single]
            if not orders:
                if hasattr(self._order_repo, "find_by_payment_attempt"):
                    single = self._order_repo.find_by_payment_attempt(
                        effective_tenant, effective_attempt_id
                    )
                    if single is not None:
                        orders = [single]
                elif hasattr(self._order_repo, "_orders"):
                    for candidate in self._order_repo._orders.get(effective_tenant, {}).values():
                        if (
                            candidate.payment_attempt_id == effective_attempt_id
                            or candidate.order_id == effective_attempt_id
                        ):
                            orders.append(candidate)
        order: Order | None = orders[0] if orders else None

        refund: Refund | None = None

        if event_type == "payment.captured":
            captured_amount_raw: Any = None
            for candidate in (
                payload.get("amount_minor"),
                data_dict.get("amount_minor"),
                payload.get("amount"),
                data_dict.get("amount"),
            ):
                if candidate is not None:
                    captured_amount_raw = candidate
                    break
            captured_currency_raw = payload.get("currency") or data_dict.get("currency")
            if isinstance(captured_amount_raw, dict):
                amount_object = captured_amount_raw
                captured_amount_raw = amount_object.get("minor_units")
                captured_currency_raw = captured_currency_raw or amount_object.get("currency")
            if captured_amount_raw is not None:
                if isinstance(captured_amount_raw, bool) or not isinstance(
                    captured_amount_raw, int
                ):
                    raise InvalidPaymentError("Webhook captured amount must be integer minor units")
                if captured_amount_raw != attempt.amount.minor_units:
                    raise InvalidPaymentError(
                        f"Webhook captured amount {captured_amount_raw} does not match "
                        f"authorized amount {attempt.amount.minor_units}"
                    )
            if (
                captured_currency_raw is not None
                and str(captured_currency_raw).upper() != attempt.amount.currency
            ):
                raise InvalidPaymentError(
                    f"Webhook captured currency '{captured_currency_raw}' does not match "
                    f"authorized currency '{attempt.amount.currency}'"
                )
            if attempt.status in (PaymentStatus.FAILED, PaymentStatus.DECLINED):
                raise InvalidPaymentError(
                    f"Cannot capture payment '{effective_attempt_id}' in terminal failure status '{attempt.status.value}'"
                )
            if attempt.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
                raise InvalidPaymentError(
                    f"Cannot capture payment '{effective_attempt_id}' in status '{attempt.status.value}'"
                )

            if attempt.status is PaymentStatus.CAPTURED:
                if (
                    order is not None
                    and self._order_repo is not None
                    and order.status is OrderStatus.PENDING_PAYMENT
                ):
                    confirmed_order = order.confirm(payment_attempt_id=effective_attempt_id)
                    self._order_repo.save(effective_tenant, confirmed_order)
                    order = confirmed_order
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": effective_attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": None,
                }

            updated_attempt = attempt.transition_to(
                PaymentStatus.CAPTURED,
                provider_payment_id=provider_payment_id,
            )
            self._repo.save(effective_tenant, updated_attempt)

            if (
                order is not None
                and self._order_repo is not None
                and order.status is OrderStatus.PENDING_PAYMENT
            ):
                confirmed_order = order.confirm(payment_attempt_id=effective_attempt_id)
                self._order_repo.save(effective_tenant, confirmed_order)
                order = confirmed_order

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_CAPTURED,
                    action_id=attempt.intent_id,
                    tenant_id=effective_tenant,
                    actor="provider_webhook",
                    data={
                        "attempt_id": effective_attempt_id,
                        "provider_payment_id": provider_payment_id,
                        "order_id": order.order_id if order else None,
                        "status": PaymentStatus.CAPTURED.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=effective_trace_id,
                )

        elif event_type == "payment.failed":
            if attempt.status is PaymentStatus.CAPTURED:
                raise InvalidPaymentError(
                    f"Cannot fail payment '{effective_attempt_id}' in status 'CAPTURED'"
                )
            if attempt.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
                raise InvalidPaymentError(
                    f"Cannot fail payment '{effective_attempt_id}' in status '{attempt.status.value}'"
                )

            if attempt.status in (PaymentStatus.FAILED, PaymentStatus.DECLINED):
                if orders and self._order_repo is not None:
                    updated_orders: list[Order] = []
                    for ord_item in orders:
                        if ord_item.status is OrderStatus.PENDING_PAYMENT:
                            cancelled_ord = ord_item.cancel(reason=failure_reason)
                            self._order_repo.save(effective_tenant, cancelled_ord)
                            updated_orders.append(cancelled_ord)
                            if self._inventory_repo is not None:
                                self._inventory_repo.release(
                                    effective_tenant, ord_item.variant_id, ord_item.quantity
                                )
                        else:
                            updated_orders.append(ord_item)
                    orders = updated_orders
                    order = orders[0] if orders else None
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": effective_attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": None,
                }

            updated_attempt = attempt.transition_to(
                PaymentStatus.FAILED,
                failure_reason=failure_reason,
            )
            self._repo.save(effective_tenant, updated_attempt)

            if orders and self._order_repo is not None:
                updated_orders = []
                for ord_item in orders:
                    if ord_item.status is OrderStatus.PENDING_PAYMENT:
                        cancelled_ord = ord_item.cancel(reason=failure_reason)
                        self._order_repo.save(effective_tenant, cancelled_ord)
                        updated_orders.append(cancelled_ord)

                        # SAGA compensation: release reserved inventory
                        if self._inventory_repo is not None:
                            self._inventory_repo.release(
                                effective_tenant, ord_item.variant_id, ord_item.quantity
                            )
                        if self._audit_ledger is not None:
                            self._audit_ledger.record_event(
                                event_type=AuditEventType.ORDER_CANCELLED,
                                action_id=attempt.intent_id,
                                tenant_id=effective_tenant,
                                actor="provider_webhook",
                                data={
                                    "order_id": ord_item.order_id,
                                    "reason": failure_reason,
                                },
                                agent_id=ord_item.agent_id,
                                trace_id=effective_trace_id,
                            )
                    else:
                        updated_orders.append(ord_item)
                orders = updated_orders
                order = orders[0] if orders else None
            elif self._inventory_repo is not None:
                variant_id = str(payload.get("variant_id") or data_dict.get("variant_id") or "")
                if variant_id:
                    qty = int(payload.get("quantity") or data_dict.get("quantity") or 1)
                    self._inventory_repo.release(effective_tenant, variant_id, qty)

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_FAILED,
                    action_id=attempt.intent_id,
                    tenant_id=effective_tenant,
                    actor="provider_webhook",
                    data={
                        "attempt_id": effective_attempt_id,
                        "failure_reason": failure_reason,
                        "order_id": order.order_id if order else None,
                        "status": PaymentStatus.FAILED.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=effective_trace_id,
                )

        elif event_type == "payment.refunded":
            if attempt.status not in (
                PaymentStatus.CAPTURED,
                PaymentStatus.PARTIALLY_REFUNDED,
                PaymentStatus.REFUNDED,
            ):
                raise InvalidPaymentError(
                    f"Cannot refund payment '{effective_attempt_id}' in status '{attempt.status.value}'; only CAPTURED payments can be refunded"
                )

            if attempt.status is PaymentStatus.REFUNDED:
                existing_refund_id = payload.get("refund_id") or data_dict.get("refund_id")
                return {
                    "status": "PROCESSED",
                    "event_type": event_type,
                    "attempt_id": effective_attempt_id,
                    "payment_status": attempt.status.value,
                    "order_id": order.order_id if order else None,
                    "order_status": order.status.value if order else None,
                    "refund_id": str(existing_refund_id) if existing_refund_id else None,
                }

            # Validate the webhook-declared refund amount when present (§41):
            # positive minor units, same currency, never exceeding the captured
            # balance. Absent amount means a full refund (backwards compatible
            # with providers that omit it).
            refund_minor_raw: Any = None
            for candidate in (
                payload.get("amount_minor"),
                data_dict.get("amount_minor"),
                payload.get("refund_amount_minor"),
                data_dict.get("refund_amount_minor"),
            ):
                if candidate is not None:
                    refund_minor_raw = candidate
                    break
            refund_currency_raw: Any = payload.get("currency") or data_dict.get("currency")
            from agentpay.domain.money import Money as _Money

            refund_amount: _Money
            target_status: PaymentStatus
            if refund_minor_raw is not None:
                if isinstance(refund_minor_raw, bool) or not isinstance(refund_minor_raw, int):
                    raise InvalidPaymentError(
                        "Webhook refund amount_minor must be an integer "
                        f"(minor units), got {refund_minor_raw!r}"
                    )
                if refund_minor_raw <= 0:
                    raise InvalidPaymentError(
                        f"Webhook refund amount_minor must be positive, got {refund_minor_raw}"
                    )
                prior_refunded_minor = 0
                if self._refund_repo is not None:
                    prior_refunded_minor = sum(
                        refund.amount.minor_units
                        for refund in self._refund_repo.list_for_payment_attempt(
                            effective_tenant, effective_attempt_id
                        )
                    )
                if prior_refunded_minor + refund_minor_raw > attempt.amount.minor_units:
                    raise InvalidPaymentError(
                        f"Cumulative webhook refunds of {prior_refunded_minor + refund_minor_raw} "
                        f"{attempt.amount.currency} exceed captured balance {attempt.amount.minor_units} "
                        f"{attempt.amount.currency}"
                    )
                if refund_currency_raw is not None and str(refund_currency_raw).upper() != (
                    attempt.amount.currency
                ):
                    raise InvalidPaymentError(
                        f"Webhook refund currency '{refund_currency_raw}' does not match "
                        f"captured currency '{attempt.amount.currency}'"
                    )
                refund_amount = _Money(
                    minor_units=refund_minor_raw, currency=attempt.amount.currency
                )
                cumulative_refunded_minor = prior_refunded_minor + refund_minor_raw
                target_status = (
                    PaymentStatus.REFUNDED
                    if cumulative_refunded_minor == attempt.amount.minor_units
                    else PaymentStatus.PARTIALLY_REFUNDED
                )
            else:
                refund_amount = attempt.amount
                target_status = PaymentStatus.REFUNDED

            updated_attempt = attempt.transition_to(target_status)
            self._repo.save(effective_tenant, updated_attempt)

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
                payment_attempt_id=effective_attempt_id,
                tenant_id=effective_tenant,
                amount=refund_amount,
                reason=refund_reason,
            )

            if self._refund_repo is not None:
                self._refund_repo.save(effective_tenant, refund)

            # SAGA restock if applicable. A partial payment refund does not
            # cancel the order or restock inventory; only a full REFUNDED does.
            if orders and self._order_repo is not None and target_status is PaymentStatus.REFUNDED:
                updated_orders = []
                for ord_item in orders:
                    if ord_item.status is OrderStatus.CONFIRMED:
                        refunded_order = Order(
                            order_id=ord_item.order_id,
                            tenant_id=ord_item.tenant_id,
                            merchant_id=ord_item.merchant_id,
                            buyer_id=ord_item.buyer_id,
                            agent_id=ord_item.agent_id,
                            variant_id=ord_item.variant_id,
                            quantity=ord_item.quantity,
                            total_amount=ord_item.total_amount,
                            status=OrderStatus.REFUNDED,
                            payment_attempt_id=ord_item.payment_attempt_id,
                            created_at=ord_item.created_at,
                        )
                        self._order_repo.save(effective_tenant, refunded_order)
                        updated_orders.append(refunded_order)

                        if self._inventory_repo is not None:
                            self._inventory_repo.release(
                                effective_tenant, ord_item.variant_id, ord_item.quantity
                            )
                    else:
                        updated_orders.append(ord_item)
                orders = updated_orders
                order = orders[0] if orders else None
            elif self._inventory_repo is not None and target_status is PaymentStatus.REFUNDED:
                variant_id = str(payload.get("variant_id") or data_dict.get("variant_id") or "")
                if variant_id:
                    qty = int(payload.get("quantity") or data_dict.get("quantity") or 1)
                    self._inventory_repo.release(effective_tenant, variant_id, qty)

            if self._audit_ledger is not None:
                self._audit_ledger.record_event(
                    event_type=AuditEventType.PAYMENT_REFUNDED,
                    action_id=attempt.intent_id,
                    tenant_id=effective_tenant,
                    actor="provider_webhook",
                    data={
                        "attempt_id": effective_attempt_id,
                        "refund_id": refund.refund_id,
                        "amount_minor": refund.amount.minor_units,
                        "reason": refund.reason,
                        "order_id": order.order_id if order else None,
                        "status": target_status.value,
                    },
                    agent_id=order.agent_id if order else None,
                    trace_id=effective_trace_id,
                )

        else:
            raise InvalidPaymentError(f"Unsupported webhook event type '{event_type}'")

        return {
            "status": "PROCESSED",
            "tenant_id": effective_tenant,
            "event_type": event_type,
            "attempt_id": effective_attempt_id,
            "payment_status": updated_attempt.status.value,
            "order_id": order.order_id if order else None,
            "order_status": order.status.value if order else None,
            "refund_id": refund.refund_id if refund else None,
        }

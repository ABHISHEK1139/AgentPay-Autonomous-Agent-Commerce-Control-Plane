"""Canonical Purchase Orchestrator use case (§6, §74).

Coordinates the canonical 12-step vertical slice flow:
1. ActionRequest receipt & Idempotency verification (§42)
2. Agent Identity & Authorization validation (§15, §16)
3. Catalog lookup, Evidence Provenance & Conflict Detection (§32, §33)
4. Decision Intelligence / JEV advisory risk assessment (§28)
5. Deterministic Spending Policy evaluation (§18, §19)
6. Human Approval creation / binding gating (§20)
7. Inventory atomic reservation (§44)
8. Payment execution & state tracking (§39, §41)
9. Order creation & confirmation (§41)
10. Saga compensation rollback on downstream failure (§44)
11. Tamper-evident Audit Ledger event emission at each milestone (§45)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agentpay.application.ports.approval_port import (
    ApprovalRepository,
    DecisionEngine,
)
from agentpay.application.ports.audit_port import AuditLedger
from agentpay.application.ports.commerce_port import (
    EvidenceRepository,
    InventoryRepository,
    MerchantRepository,
    ProductRepository,
    VariantRepository,
)
from agentpay.application.ports.identity_port import (
    AgentPermissionsRepository,
    AgentRepository,
    TenantRepository,
)
from agentpay.application.ports.payment_port import (
    IdempotencyStore,
    OrderRepository,
    PaymentProvider,
    PaymentRepository,
)
from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.domain.action_request import ActionRequest, ActionStatus
from agentpay.domain.approval import Approval
from agentpay.domain.audit import AuditEventType
from agentpay.domain.decision import Decision, DecisionContext
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    DomainError,
    InsufficientInventoryError,
    InvalidCommerceError,
)
from agentpay.domain.evidence import (
    EvidenceConflict,
    EvidenceConflictDetector,
)
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
)
from agentpay.domain.policy import (
    PolicyDecision,
    SpendingLimits,
    SpendingPolicyEngine,
    SpendingPolicyRequest,
)


@dataclass(frozen=True, slots=True)
class PurchaseExecutionResult:
    """The outcome of a canonical purchase orchestration request (§6, §74)."""

    action_id: str
    tenant_id: str
    status: ActionStatus
    decision: PolicyDecision
    order: Order | None = None
    payment: PaymentAttempt | None = None
    approval: Approval | None = None
    decision_summary: Decision | None = None
    conflicts: tuple[EvidenceConflict, ...] = field(default_factory=tuple)
    rejection_reason: str | None = None


class PurchaseOrchestrator:
    """The canonical domain use-case orchestrator for AgentPay commerce transactions."""

    def __init__(
        self,
        tenant_repo: TenantRepository | None = None,
        agent_repo: AgentRepository | None = None,
        permissions_repo: AgentPermissionsRepository | None = None,
        auth_service: AgentAuthorizationService | None = None,
        merchant_repo: MerchantRepository | None = None,
        product_repo: ProductRepository | None = None,
        variant_repo: VariantRepository | None = None,
        inventory_repo: InventoryRepository | None = None,
        evidence_repo: EvidenceRepository | None = None,
        decision_engine: DecisionEngine | None = None,
        policy_engine: SpendingPolicyEngine | None = None,
        approval_service: ApprovalService | None = None,
        approval_repo: ApprovalRepository | None = None,
        payment_provider: PaymentProvider | None = None,
        payment_repo: PaymentRepository | None = None,
        order_repo: OrderRepository | None = None,
        idempotency_store: IdempotencyStore | None = None,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._agent_repo = agent_repo
        self._permissions_repo = permissions_repo
        self._auth_service = auth_service or AgentAuthorizationService(
            tenant_repo=tenant_repo,
            agent_repo=agent_repo,
            permissions_repo=permissions_repo,
        )
        self._merchant_repo = merchant_repo
        self._product_repo = product_repo
        self._variant_repo = variant_repo
        self._inventory_repo = inventory_repo
        self._evidence_repo = evidence_repo
        self._decision_engine = decision_engine
        self._policy_engine = policy_engine or SpendingPolicyEngine()
        self._approval_repo = approval_repo
        self._approval_service = approval_service or ApprovalService(approval_repo=approval_repo)
        self._payment_provider = payment_provider
        self._payment_repo = payment_repo
        self._order_repo = order_repo
        self._idempotency_store = idempotency_store
        self._audit_ledger = audit_ledger

    def _audit(
        self,
        event_type: AuditEventType,
        action: ActionRequest,
        tenant_id: str,
        actor: str,
        data: dict[str, object],
    ) -> None:
        if self._audit_ledger is not None:
            self._audit_ledger.record_event(
                event_type=event_type,
                action_id=action.action_id,
                tenant_id=tenant_id,
                actor=actor,
                data=data,
                agent_id=action.agent_id,
            )

    def execute_purchase(
        self,
        action: ActionRequest,
        tenant_id: str,
        variant_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
    ) -> PurchaseExecutionResult:
        """Execute the canonical end-to-end purchase flow (§74)."""
        # 1. Multi-tenant context validation (§49, §50)
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest tenant does not match context")

        # 1b. Idempotency Check (§42)
        if self._idempotency_store is not None and idempotency_key is not None:
            existing = self._idempotency_store.get(tenant_id, idempotency_key)
            if existing is not None:
                cached_order = None
                order_id = existing.result_data.get("order_id")
                if order_id and self._order_repo is not None:
                    cached_order = self._order_repo.get(tenant_id, order_id)
                cached_payment = None
                payment_id = existing.result_data.get("payment_id")
                if payment_id and self._payment_repo is not None:
                    cached_payment = self._payment_repo.get(tenant_id, payment_id)
                status_raw = existing.result_data.get("status", "COMPLETED")
                cached_status = (
                    ActionStatus(status_raw)
                    if status_raw in ActionStatus._value2member_map_
                    else ActionStatus.COMPLETED
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=cached_status,
                    decision=PolicyDecision.ALLOW,
                    order=cached_order,
                    payment=cached_payment,
                )

        # 2. Audit Intent receipt (§45)
        self._audit(
            AuditEventType.ACTION_REQUESTED,
            action,
            tenant_id,
            actor=action.agent_id,
            data={"action": action.action, "amount": str(action.amount)},
        )

        # 3. Agent Identity & Authorization validation (§15, §16)
        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, action.agent_id)

        if agent is not None:
            try:
                self._auth_service.authorize_action(agent, action, tenant_id)
            except DomainError as err:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="auth_service",
                    data={"decision": "DENIED", "error": str(err)},
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=f"Authorization rejected: {err}",
                )

        # 4. Commerce & Evidence Verification (§30, §32, §33)
        variant = None
        if self._variant_repo is not None:
            variant = self._variant_repo.get(tenant_id, variant_id)

        inventory = None
        if self._inventory_repo is not None:
            inventory = self._inventory_repo.get(tenant_id, variant_id)

        if variant is not None:
            if self._product_repo is not None:
                product = self._product_repo.get(tenant_id, variant.product_id)
                if product is not None and self._permissions_repo is not None and agent is not None:
                    perms = self._permissions_repo.get(tenant_id, agent.agent_id)
                    if perms is not None and not perms.allows_category(product.category):
                        self._audit(
                            AuditEventType.POLICY_EVALUATED,
                            action,
                            tenant_id,
                            actor="auth_service",
                            data={"decision": "DENIED", "category": product.category},
                        )
                        return PurchaseExecutionResult(
                            action_id=action.action_id,
                            tenant_id=tenant_id,
                            status=ActionStatus.FAILED,
                            decision=PolicyDecision.DENY,
                            rejection_reason=f"Category '{product.category}' is not permitted for agent '{agent.agent_id}'",
                        )

            if action.amount is not None:
                expected_total = variant.price.multiply(quantity)
            conflicts = EvidenceConflictDetector.detect_all_conflicts(
                tenant_id=tenant_id,
                resource_id=variant_id,
                asserted_price=action.amount,
                merchant_price=expected_total,
                asserted_available=None,
                actual_available=inventory.effective_available if inventory else 0,
                requested_qty=quantity,
            )
            if conflicts:
                self._audit(
                    AuditEventType.CONFLICT_DETECTED,
                    action,
                    tenant_id,
                    actor="conflict_detector",
                    data={
                        "conflict_count": len(conflicts),
                        "first_conflict": conflicts[0].conflict_type.value,
                    },
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    conflicts=conflicts,
                    rejection_reason="Evidence conflict detected (price/inventory mismatch)",
                )

        # 5. Advisory Decision Intelligence (§28)
        decision_summary = None
        if self._decision_engine is not None:
            context = DecisionContext(
                action_id=action.action_id,
                tenant_id=tenant_id,
                agent_id=action.agent_id,
                amount=action.amount,
                category=action.resource.type,
            )
            decision_summary = self._decision_engine.evaluate(context)
            self._audit(
                AuditEventType.JEV_EVALUATED,
                action,
                tenant_id,
                actor=decision_summary.model_provider,
                data={
                    "recommendation": decision_summary.recommendation.value,
                    "risk_level": decision_summary.risk_level.value,
                    "confidence": decision_summary.confidence,
                },
            )

        # 6. Deterministic Spending Policy Evaluation (§18, §19)
        # Authoritative spending policy check
        limits = SpendingLimits(
            per_transaction_minor=200000,  # ₹2,000 autonomous ceiling (§5.1, §112)
            daily_minor=500000,  # ₹5,000 daily ceiling
            requires_approval_above_minor=200000,
        )
        if self._permissions_repo is not None and agent is not None:
            perms = self._permissions_repo.get(tenant_id, agent.agent_id)
            if perms is not None:
                limits = perms.limits

        if action.amount is None:
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason="Action has no amount specified",
            )

        policy_result = self._policy_engine.evaluate(
            request=SpendingPolicyRequest(amount=action.amount),
            limits=limits,
            policy_version="spending-policy-v1",
        )
        self._audit(
            AuditEventType.POLICY_EVALUATED,
            action,
            tenant_id,
            actor="policy_engine",
            data={
                "decision": policy_result.decision.value,
                "requires_approval": policy_result.requires_approval,
            },
        )

        if policy_result.decision is PolicyDecision.DENY:
            reason_msg = (
                policy_result.reasons[0].message
                if policy_result.reasons
                else "Policy limit exceeded"
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                decision_summary=decision_summary,
                rejection_reason=reason_msg,
            )

        # 7. Human Approval Gating if REVIEW required (§20)
        if policy_result.decision is PolicyDecision.REVIEW:
            approval = self._approval_service.create_approval(
                action=action,
                tenant_id=tenant_id,
                reason="Amount exceeds autonomous threshold; awaiting human review",
            )
            self._audit(
                AuditEventType.APPROVAL_REQUESTED,
                action,
                tenant_id,
                actor="system",
                data={"approval_id": approval.approval_id, "amount": str(action.amount)},
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.AWAITING_APPROVAL,
                decision=PolicyDecision.REVIEW,
                approval=approval,
                decision_summary=decision_summary,
            )

        # 8. Execution Pipeline (PolicyDecision.ALLOW)
        return self._execute_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            variant_id=variant_id,
            quantity=quantity,
            decision_summary=decision_summary,
            idempotency_key=idempotency_key or f"idem_{action.action_id}",
        )

    def _execute_order_and_payment_pipeline(
        self,
        action: ActionRequest,
        tenant_id: str,
        variant_id: str,
        quantity: int,
        decision_summary: Decision | None = None,
        approval: Approval | None = None,
        idempotency_key: str | None = None,
    ) -> PurchaseExecutionResult:
        if action.amount is None:
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason="Amount missing",
            )

        # 1. Atomic Inventory Reservation (§44)
        if self._inventory_repo is not None:
            try:
                self._inventory_repo.reserve(tenant_id, variant_id, quantity)
            except InsufficientInventoryError as err:
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=str(err),
                )

        # 2. Payment Execution (§39, §41)
        payment_attempt = None
        if self._payment_provider is not None:
            idem_key = idempotency_key or f"idem_{action.action_id}"
            payment_attempt = self._payment_provider.create_payment(
                tenant_id=tenant_id,
                intent_id=f"intent_{action.action_id}",
                amount=action.amount,
                idempotency_key=idem_key,
            )

            if self._payment_repo is not None:
                self._payment_repo.save(tenant_id, payment_attempt)

            self._audit(
                AuditEventType.PAYMENT_CREATED,
                action,
                tenant_id,
                actor="payment_provider",
                data={
                    "payment_id": payment_attempt.attempt_id,
                    "status": payment_attempt.status.value,
                    "amount": str(action.amount),
                },
            )

            # Check payment success
            if payment_attempt.status is PaymentStatus.DECLINED:
                # SAGA COMPENSATION: Release reserved inventory (§44)
                if self._inventory_repo is not None:
                    self._inventory_repo.release(tenant_id, variant_id, quantity)

                self._audit(
                    AuditEventType.PAYMENT_FAILED,
                    action,
                    tenant_id,
                    actor="payment_provider",
                    data={"payment_id": payment_attempt.attempt_id, "status": "DECLINED"},
                )

                cancelled_order = Order(
                    order_id=f"ord_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    merchant_id=action.resource.id,
                    buyer_id=action.principal_id,
                    agent_id=action.agent_id,
                    variant_id=variant_id,
                    quantity=quantity,
                    total_amount=action.amount,
                    status=OrderStatus.CANCELLED,
                    payment_attempt_id=payment_attempt.attempt_id,
                )
                if self._order_repo is not None:
                    self._order_repo.save(tenant_id, cancelled_order)

                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={"order_id": cancelled_order.order_id, "reason": "Payment declined"},
                )

                if self._idempotency_store is not None and idempotency_key is not None:
                    self._idempotency_store.save(
                        tenant_id,
                        IdempotencyRecord(
                            key=idempotency_key,
                            tenant_id=tenant_id,
                            operation_type="purchase",
                            request_hash=action.action_id,
                            result_data={
                                "order_id": cancelled_order.order_id,
                                "payment_id": payment_attempt.attempt_id,
                                "status": "FAILED",
                            },
                        ),
                    )

                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.ALLOW,
                    order=cancelled_order,
                    payment=payment_attempt,
                    rejection_reason="Payment was declined by provider",
                )

            if payment_attempt.status is PaymentStatus.UNKNOWN:
                # Timeout state - fails closed awaiting reconciliation (§43)
                self._audit(
                    AuditEventType.PAYMENT_UNKNOWN,
                    action,
                    tenant_id,
                    actor="payment_provider",
                    data={"payment_id": payment_attempt.attempt_id, "status": "UNKNOWN"},
                )

                if self._idempotency_store is not None and idempotency_key is not None:
                    self._idempotency_store.save(
                        tenant_id,
                        IdempotencyRecord(
                            key=idempotency_key,
                            tenant_id=tenant_id,
                            operation_type="purchase",
                            request_hash=action.action_id,
                            result_data={
                                "payment_id": payment_attempt.attempt_id,
                                "status": "FAILED",
                            },
                        ),
                    )

                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.ALLOW,
                    payment=payment_attempt,
                    rejection_reason="Payment gateway timeout; pending reconciliation",
                )

        # 3. Order Creation and Confirmation (§41)
        order_id = f"ord_{uuid.uuid4().hex[:16]}"
        order = Order(
            order_id=order_id,
            tenant_id=tenant_id,
            merchant_id=action.resource.id,
            buyer_id=action.principal_id,
            agent_id=action.agent_id,
            variant_id=variant_id,
            quantity=quantity,
            total_amount=action.amount,
            status=OrderStatus.CONFIRMED,
            payment_attempt_id=payment_attempt.attempt_id if payment_attempt else None,
        )

        if self._order_repo is not None:
            self._order_repo.save(tenant_id, order)

        self._audit(
            AuditEventType.ORDER_CREATED,
            action,
            tenant_id,
            actor="order_service",
            data={"order_id": order.order_id, "status": order.status.value},
        )

        # 4. Save Idempotency record (§42)
        if self._idempotency_store is not None and idempotency_key is not None:
            self._idempotency_store.save(
                tenant_id,
                IdempotencyRecord(
                    key=idempotency_key,
                    tenant_id=tenant_id,
                    operation_type="purchase",
                    request_hash=action.action_id,
                    result_data={
                        "order_id": order.order_id,
                        "payment_id": payment_attempt.attempt_id if payment_attempt else None,
                        "status": "COMPLETED",
                    },
                ),
            )

        return PurchaseExecutionResult(
            action_id=action.action_id,
            tenant_id=tenant_id,
            status=ActionStatus.COMPLETED,
            decision=PolicyDecision.ALLOW,
            order=order,
            payment=payment_attempt,
            approval=approval,
            decision_summary=decision_summary,
        )

    def execute_approved_purchase(
        self,
        approval_id: str,
        action: ActionRequest,
        tenant_id: str,
        variant_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
    ) -> PurchaseExecutionResult:
        """Execute a purchase that was previously approved by a human (§20)."""
        if self._approval_repo is None:
            raise InvalidCommerceError("Approval repository not configured")

        approval = self._approval_repo.get(tenant_id, approval_id)
        if approval is None:
            raise InvalidCommerceError(
                f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
            )

        # STRICT BINDING VERIFICATION (§0.4, §20)
        self._approval_service.verify_binding(approval, action)

        if not approval.is_approved:
            raise ApprovalBindingMismatchError(
                f"Approval is in status '{approval.status.value}', not APPROVED"
            )

        self._audit(
            AuditEventType.APPROVAL_GRANTED,
            action,
            tenant_id,
            actor=approval.approver_id or "human_approver",
            data={"approval_id": approval_id},
        )

        return self._execute_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            variant_id=variant_id,
            quantity=quantity,
            approval=approval,
            idempotency_key=idempotency_key or f"idem_{action.action_id}",
        )

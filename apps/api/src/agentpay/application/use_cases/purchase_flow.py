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

import logging
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from agentpay.adapters.in_memory.payment_repository import (
    InMemoryProviderTransactionMappingRepository,
)
from agentpay.application.ports.approval_port import (
    ApprovalRepository,
    DecisionEngine,
)
from agentpay.application.ports.audit_port import AuditLedger
from agentpay.application.ports.commerce_port import (
    CartRepository,
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
    ProviderTransactionMappingRepository,
)
from agentpay.application.services.approval_service import ApprovalService
from agentpay.application.services.identity_service import AgentAuthorizationService
from agentpay.domain.action_request import ActionRequest, ActionStatus, Intent, ResourceRef
from agentpay.domain.approval import Approval, ApprovalAlreadyConsumedError, ApprovalStatus
from agentpay.domain.audit import AuditEventType
from agentpay.domain.cart import Cart, CartItem, CartStatus
from agentpay.domain.commerce import Inventory, MerchantStatus, Product, ProductVariant
from agentpay.domain.decision import Decision, DecisionContext
from agentpay.domain.errors import (
    ApprovalBindingMismatchError,
    CrossTenantAccessError,
    CurrencyMismatchError,
    DomainError,
    IdempotencyConflictError,
    InsufficientInventoryError,
    InvalidCommerceError,
    InvalidIdentityError,
    ToolExecutionUnavailableError,
)
from agentpay.domain.evidence import (
    Evidence,
    EvidenceConflict,
    EvidenceConflictDetector,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceType,
)
from agentpay.domain.money import Money, total_money
from agentpay.domain.payments import (
    IdempotencyRecord,
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderTransactionMapping,
)
from agentpay.domain.policy import (
    PolicyDecision,
    SpendingLimits,
    SpendingPolicyEngine,
    SpendingPolicyRequest,
)
from agentpay.domain.safety import AgentLoopTracker
from agentpay.domain.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    build_default_tool_registry,
)

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class CartCheckoutResult:
    """The outcome of a canonical multi-item cart checkout request (ADR-0006)."""

    action_id: str
    tenant_id: str
    status: ActionStatus
    decision: PolicyDecision
    cart: Cart | None = None
    orders: tuple[Order, ...] = field(default_factory=tuple)
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
        cart_repo: CartRepository | None = None,
        decision_engine: DecisionEngine | None = None,
        policy_engine: SpendingPolicyEngine | None = None,
        approval_service: ApprovalService | None = None,
        approval_repo: ApprovalRepository | None = None,
        payment_provider: PaymentProvider | None = None,
        payment_repo: PaymentRepository | None = None,
        order_repo: OrderRepository | None = None,
        idempotency_store: IdempotencyStore | None = None,
        audit_ledger: AuditLedger | None = None,
        tool_registry: ToolRegistry | None = None,
        provider_mapping_repo: ProviderTransactionMappingRepository | None = None,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._agent_repo = agent_repo
        self._permissions_repo = permissions_repo
        self._tool_registry = (
            tool_registry if tool_registry is not None else build_default_tool_registry()
        )
        self._auth_service = auth_service or AgentAuthorizationService(
            tenant_repo=tenant_repo,
            agent_repo=agent_repo,
            permissions_repo=permissions_repo,
            tool_registry=self._tool_registry,
        )
        self._merchant_repo = merchant_repo
        self._product_repo = product_repo
        self._variant_repo = variant_repo
        self._inventory_repo = inventory_repo
        self._evidence_repo = evidence_repo
        self._cart_repo = cart_repo
        self._decision_engine = decision_engine
        self._policy_engine = policy_engine or SpendingPolicyEngine()
        self._approval_repo = approval_repo
        self._approval_service = approval_service or ApprovalService(approval_repo=approval_repo)
        self._payment_provider = payment_provider
        self._payment_repo = payment_repo
        self._order_repo = order_repo
        self._idempotency_store = idempotency_store
        self._audit_ledger = audit_ledger
        self._provider_mapping_repo: ProviderTransactionMappingRepository = (
            provider_mapping_repo
            if provider_mapping_repo is not None
            else InMemoryProviderTransactionMappingRepository()
        )
        self._approval_lock = threading.Lock()
        self._loop_trackers: dict[str, AgentLoopTracker] = {}
        # (tenant_id, agent_id, utc_date_iso) -> cumulative minor units spent that day.
        # Backs the daily spending limit: SpendingPolicyRequest.spent_today_minor is
        # populated from this ledger so the daily cap is enforced across requests (§18).
        self._daily_spend: dict[tuple[str, str, str], int] = {}
        self._daily_spend_lock = threading.Lock()
        # Striped per-(tenant, cart) checkout locks: two concurrent checkouts of the
        # same ACTIVE cart must never both reach the payment pipeline (§43, §177).
        # Process-local by design; a multi-process deployment would additionally
        # require a database-level atomic claim on the cart.
        self._cart_checkout_locks: tuple[threading.RLock, ...] = tuple(
            threading.RLock() for _ in range(64)
        )

    def _cart_checkout_lock(self, tenant_id: str, cart_id: str) -> threading.RLock:
        """Return the striped lock serializing checkout for this cart (ADR-0006)."""
        key = f"{tenant_id}:{cart_id}"
        return self._cart_checkout_locks[hash(key) % len(self._cart_checkout_locks)]

    def _get_spent_today_minor(self, tenant_id: str, agent_id: str) -> int:
        """Cumulative minor units this agent has spent in the tenant today (UTC)."""
        day = datetime.now(UTC).date().isoformat()
        with self._daily_spend_lock:
            return self._daily_spend.get((tenant_id, agent_id, day), 0)

    def _record_daily_spend(self, tenant_id: str, agent_id: str, amount_minor: int) -> None:
        """Add a completed purchase to the agent's daily spend ledger."""
        day = datetime.now(UTC).date().isoformat()
        with self._daily_spend_lock:
            key = (tenant_id, agent_id, day)
            self._daily_spend[key] = self._daily_spend.get(key, 0) + amount_minor

    def _resolve_action_merchant_id(
        self, tenant_id: str, action: ActionRequest, variant_id: str | None
    ) -> str | None:
        """Resolve the true merchant for an action via variant→product→merchant (§20, §30).

        Mirrors the resolution applied when an approval is created so binding
        verification compares the same merchant identity on both sides.
        """
        product = None
        if variant_id and self._variant_repo is not None and self._product_repo is not None:
            variant = self._variant_repo.get(tenant_id, variant_id)
            if variant is not None:
                product = self._product_repo.get(tenant_id, variant.product_id)
        if product is None and self._product_repo is not None and action.resource.id:
            product = self._product_repo.get(tenant_id, action.resource.id)
        if product is not None and product.merchant_id:
            return product.merchant_id
        return None

    def execute_tool(
        self,
        tenant_id: str,
        agent_id: str,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a registered and authorized tool on behalf of an agent (§26, §27)."""
        params = parameters or {}

        # 1. Retrieve agent within tenant partition (§49, §50)
        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, agent_id)

        if agent is None:
            raise InvalidIdentityError(f"Agent '{agent_id}' not found in tenant '{tenant_id}'")

        # 2. Authorize tool via authorization service & tool registry (§26, §27)
        tool_def: ToolDefinition = self._auth_service.authorize_tool_call(agent, tool_name)

        # 3. Track loop budget (§64)
        tracker_key = f"{tenant_id}:{agent_id}"
        tracker = self._loop_trackers.setdefault(tracker_key, AgentLoopTracker())
        tracker.record_tool_call()

        # 4. Audit tool execution attempt (§45)
        if self._audit_ledger is not None:
            self._audit_ledger.record_event(
                event_type=AuditEventType.ACTION_REQUESTED,
                action_id=f"tool_exec_{uuid.uuid4().hex[:12]}",
                tenant_id=tenant_id,
                actor=agent_id,
                data={
                    "tool_name": tool_name,
                    "risk_tier": tool_def.risk_tier.value,
                    "parameters": params,
                },
                agent_id=agent_id,
                trace_id=trace_id,
            )

        # 5. Dispatch based on tool name
        result_data: dict[str, Any] = {}
        if tool_name == "search_products":
            query = str(params.get("query", ""))
            products = []
            if self._product_repo is not None:
                products = [
                    {
                        "product_id": p.product_id,
                        "name": p.name,
                        "category": p.category,
                        "merchant_id": p.merchant_id,
                    }
                    for p in self._product_repo.search(tenant_id, query)
                ]
            result_data = {"products": products}
        elif tool_name == "get_product":
            product_id = str(params.get("product_id", ""))
            p = self._product_repo.get(tenant_id, product_id) if self._product_repo else None
            result_data = {
                "product": {
                    "product_id": p.product_id,
                    "name": p.name,
                    "category": p.category,
                    "description": p.description,
                }
                if p
                else None
            }
        elif tool_name == "check_inventory":
            variant_id = str(params.get("variant_id", ""))
            inv = self._inventory_repo.get(tenant_id, variant_id) if self._inventory_repo else None
            result_data = {
                "variant_id": variant_id,
                "available": inv.available if inv else 0,
                "reserved": inv.reserved if inv else 0,
            }
        elif tool_name == "check_payment_status":
            attempt_id = str(params.get("payment_attempt_id", ""))
            pay = self._payment_repo.get(tenant_id, attempt_id) if self._payment_repo else None
            result_data = {
                "payment_attempt_id": attempt_id,
                "status": pay.status.value if pay else "UNKNOWN",
            }
        elif tool_name == "get_evidence":
            resource_id = str(
                params.get("product_id")
                or params.get("resource_id")
                or params.get("variant_id")
                or ""
            )
            result_data = self.list_catalog_evidence(tenant_id, resource_id)
        elif tool_name == "compare_products":
            raw_ids = params.get("product_ids")
            if not isinstance(raw_ids, list) or len(raw_ids) < 2:
                raise InvalidCommerceError(
                    "compare_products requires a list of at least 2 product_ids"
                )
            comparisons: list[dict[str, Any]] = []
            for raw_id in raw_ids:
                product_id = str(raw_id)
                p = (
                    self._product_repo.get(tenant_id, product_id)
                    if self._product_repo is not None
                    else None
                )
                if p is None:
                    comparisons.append({"product_id": product_id, "found": False})
                    continue
                variant_rows: list[dict[str, Any]] = []
                if self._variant_repo is not None:
                    for variant in self._variant_repo.list_by_product(tenant_id, p.product_id):
                        inv = (
                            self._inventory_repo.get(tenant_id, variant.variant_id)
                            if self._inventory_repo is not None
                            else None
                        )
                        variant_rows.append(
                            {
                                "variant_id": variant.variant_id,
                                "sku": variant.sku,
                                "price_minor": variant.price.minor_units,
                                "currency": variant.price.currency,
                                "available": inv.effective_available if inv else 0,
                            }
                        )
                variant_rows.sort(key=lambda row: (row["price_minor"], row["variant_id"]))
                lowest = variant_rows[0] if variant_rows else None
                comparisons.append(
                    {
                        "product_id": p.product_id,
                        "found": True,
                        "name": p.name,
                        "category": p.category,
                        "merchant_id": p.merchant_id,
                        "lowest_price_minor": lowest["price_minor"] if lowest else None,
                        "currency": lowest["currency"] if lowest else None,
                        "variants": variant_rows,
                    }
                )
            comparisons.sort(
                key=lambda c: (
                    0 if c.get("lowest_price_minor") is not None else (1 if c["found"] else 2),
                    c.get("lowest_price_minor") or 0,
                    c["product_id"],
                )
            )
            result_data = {"products": comparisons}
        elif tool_name == "calculate_total":
            variant_id = str(params.get("variant_id", ""))
            qty_raw = params.get("quantity", 1)
            if isinstance(qty_raw, bool) or not isinstance(qty_raw, int) or qty_raw <= 0:
                raise InvalidCommerceError("calculate_total requires a positive integer quantity")
            v = (
                self._variant_repo.get(tenant_id, variant_id)
                if (self._variant_repo is not None and variant_id)
                else None
            )
            if v is None:
                raise InvalidCommerceError(
                    f"Variant '{variant_id}' not found in tenant '{tenant_id}'"
                )
            total = v.price.multiply(qty_raw)
            result_data = {
                "variant_id": v.variant_id,
                "quantity": qty_raw,
                "unit_price_minor": v.price.minor_units,
                "total_minor": total.minor_units,
                "currency": total.currency,
                "total_display": total.format_amount(),
            }
        elif tool_name == "system_health":
            result_data = {"status": "healthy", "service": "AgentPay Control Plane"}
        else:
            # Registered-but-unimplemented tools (mutating financial or
            # administrative flows belong to the canonical action endpoints)
            # must fail closed rather than report fake success (§2.4, §26, §135).
            raise ToolExecutionUnavailableError(
                f"Tool '{tool_def.name}' is registered but has no executable implementation "
                "on this surface; use the canonical action endpoints "
                "(POST /api/v1/actions or POST /api/v1/approvals/{id}/execute) instead"
            )

        return {
            "tool_name": tool_def.name,
            "risk_tier": tool_def.risk_tier.value,
            "required_capability": tool_def.required_capability,
            "status": "success",
            "result": result_data,
        }

    @staticmethod
    def _serialize_evidence(evidence: Evidence) -> dict[str, Any]:
        return {
            "evidence_id": evidence.evidence_id,
            "resource_id": evidence.resource_id,
            "field_name": evidence.field_name,
            "value": evidence.value,
            "evidence_type": evidence.evidence_type.value,
            "source": {
                "type": evidence.source.type.value,
                "id": evidence.source.id,
                "name": evidence.source.name,
            },
            "observed_at": evidence.observed_at.isoformat(),
        }

    def list_catalog_evidence(self, tenant_id: str, resource_id: str) -> dict[str, Any]:
        """Gather stored evidence snapshots for a product or variant (§32, §38).

        Resolves the requested id as a product first, then as a variant, and
        collects every recorded evidence record for the resource and its
        variants in deterministic order. Unknown ids return an empty payload.
        """
        product = None
        variant = None
        if resource_id and self._product_repo is not None:
            product = self._product_repo.get(tenant_id, resource_id)
        if product is None and resource_id and self._variant_repo is not None:
            variant = self._variant_repo.get(tenant_id, resource_id)
            if variant is not None and self._product_repo is not None:
                product = self._product_repo.get(tenant_id, variant.product_id)

        resource_ids: list[str] = []
        if variant is not None:
            resource_ids.append(variant.variant_id)
        if product is not None:
            resource_ids.append(product.product_id)
            if variant is None and self._variant_repo is not None:
                resource_ids.extend(
                    v.variant_id
                    for v in self._variant_repo.list_by_product(tenant_id, product.product_id)
                )

        evidence_items: list[Evidence] = []
        if self._evidence_repo is not None:
            for rid in resource_ids:
                evidence_items.extend(self._evidence_repo.list_for_resource(tenant_id, rid))
        evidence_items.sort(key=lambda e: (e.observed_at, e.evidence_id))

        return {
            "resource_id": resource_id,
            "product_id": product.product_id if product is not None else None,
            "variant_id": variant.variant_id if variant is not None else None,
            "evidence": [self._serialize_evidence(e) for e in evidence_items],
        }

    def _record_catalog_evidence(
        self,
        tenant_id: str,
        variant: ProductVariant,
        product: Product | None,
        inventory: Inventory | None,
        merchant_id: str | None,
    ) -> None:
        """Persist immutable catalog evidence snapshots observed during verification (§32)."""
        if self._evidence_repo is None:
            return
        observed_at = datetime.now(UTC)
        variant_source = EvidenceSource(
            type=EvidenceSourceType.MERCHANT_CATALOG, id=variant.variant_id
        )
        records: list[Evidence] = [
            Evidence(
                evidence_id=f"evd_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                resource_id=variant.variant_id,
                field_name="price",
                value=variant.price.format_amount(),
                evidence_type=EvidenceType.PRICE,
                source=variant_source,
                observed_at=observed_at,
            )
        ]
        if inventory is not None:
            records.append(
                Evidence(
                    evidence_id=f"evd_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    resource_id=variant.variant_id,
                    field_name="available",
                    value=str(inventory.effective_available),
                    evidence_type=EvidenceType.INVENTORY,
                    source=variant_source,
                    observed_at=observed_at,
                )
            )
        if product is not None:
            product_source = EvidenceSource(
                type=EvidenceSourceType.MERCHANT_CATALOG, id=product.product_id
            )
            records.append(
                Evidence(
                    evidence_id=f"evd_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    resource_id=product.product_id,
                    field_name="category",
                    value=product.category,
                    evidence_type=EvidenceType.SPECIFICATION,
                    source=product_source,
                    observed_at=observed_at,
                )
            )
            records.append(
                Evidence(
                    evidence_id=f"evd_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    resource_id=product.product_id,
                    field_name="name",
                    value=product.name,
                    evidence_type=EvidenceType.SPECIFICATION,
                    source=product_source,
                    observed_at=observed_at,
                )
            )
        if merchant_id:
            records.append(
                Evidence(
                    evidence_id=f"evd_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    resource_id=product.product_id if product is not None else variant.variant_id,
                    field_name="merchant_id",
                    value=merchant_id,
                    evidence_type=EvidenceType.MERCHANT,
                    source=EvidenceSource(
                        type=EvidenceSourceType.MERCHANT_CATALOG,
                        id=product.product_id if product is not None else variant.variant_id,
                    ),
                    observed_at=observed_at,
                )
            )
        for record in records:
            self._evidence_repo.save(tenant_id, record)

    def _audit(
        self,
        event_type: AuditEventType,
        action: ActionRequest,
        tenant_id: str,
        actor: str,
        data: dict[str, object],
        trace_id: str | None = None,
    ) -> None:
        if self._audit_ledger is not None:
            self._audit_ledger.record_event(
                event_type=event_type,
                action_id=action.action_id,
                tenant_id=tenant_id,
                actor=actor,
                data=data,
                agent_id=action.agent_id,
                trace_id=trace_id,
            )

    def execute_purchase(
        self,
        action: ActionRequest,
        tenant_id: str,
        variant_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> PurchaseExecutionResult:
        """Execute the canonical end-to-end purchase flow (§74)."""
        # 1. Multi-tenant context validation (§49, §50)
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest tenant does not match context")

        # 1b. Idempotency Check (§42, §177 Vector 2)
        if self._idempotency_store is not None and idempotency_key is not None:
            existing = self._idempotency_store.get(tenant_id, idempotency_key)
            if existing is not None:
                # Check for payload / amount / currency conflict (§42, B-IDEM-02)
                expected_amount_minor = existing.result_data.get("amount_minor")
                if (
                    expected_amount_minor is not None
                    and action.amount is not None
                    and action.amount.minor_units != expected_amount_minor
                ):
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored amount {expected_amount_minor} != incoming amount {action.amount.minor_units}"
                    )
                expected_currency = existing.result_data.get("currency")
                if (
                    expected_currency is not None
                    and action.amount is not None
                    and action.amount.currency != expected_currency
                ):
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored currency '{expected_currency}' != incoming currency '{action.amount.currency}'"
                    )
                stored_action_id = existing.result_data.get("action_id", existing.request_hash)
                if stored_action_id and stored_action_id != action.action_id:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored action '{stored_action_id}' != incoming action '{action.action_id}'"
                    )

                cached_order = None
                order_id = existing.result_data.get("order_id")
                if order_id and self._order_repo is not None:
                    cached_order = self._order_repo.get(tenant_id, order_id)
                cached_payment = None
                payment_id = existing.result_data.get("payment_id")
                if payment_id and self._payment_repo is not None:
                    cached_payment = self._payment_repo.get(tenant_id, payment_id)
                status_raw = existing.result_data.get("status", "COMPLETED")
                try:
                    cached_status = ActionStatus(status_raw)
                except ValueError:
                    cached_status = ActionStatus.COMPLETED
                decision_raw = existing.result_data.get("decision", "ALLOW")
                try:
                    cached_decision = PolicyDecision(decision_raw)
                except ValueError:
                    cached_decision = PolicyDecision.ALLOW
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=cached_status,
                    decision=cached_decision,
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
            trace_id=trace_id,
        )

        # 3. Agent Identity & Authorization validation (§15, §16, §177 Vectors 1 & 4)
        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, action.agent_id)

        if agent is None:
            # Unregistered or missing agent -> fail closed (§14, §15, §177 Vectors 1 & 4)
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={
                    "decision": "DENIED",
                    "reason": "UNREGISTERED_AGENT",
                    "agent_id": action.agent_id,
                },
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Agent '{action.agent_id}' not found in tenant '{tenant_id}'",
            )

        try:
            self._auth_service.authorize_action(agent, action, tenant_id)
        except DomainError as err:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={"decision": "DENIED", "error": str(err)},
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Authorization rejected: {err}",
            )

        # 4. Commerce & Evidence Verification (§30, §32, §33, §177 Vectors 8 & 9)
        variant = None
        if self._variant_repo is not None:
            variant = self._variant_repo.get(tenant_id, variant_id)

        if variant is None:
            # Missing or unknown variant -> fail closed (§30, §177 Vector 8)
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="commerce_service",
                data={"decision": "DENIED", "reason": "VARIANT_NOT_FOUND"},
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason="Variant not found",
            )

        inventory = None
        if self._inventory_repo is not None:
            inventory = self._inventory_repo.get(tenant_id, variant_id)

        product = None
        if self._product_repo is not None:
            product = self._product_repo.get(tenant_id, variant.product_id)
            if product is None:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="commerce_service",
                    data={"decision": "DENIED", "reason": "PRODUCT_NOT_FOUND"},
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason="Product not found",
                )
            if self._permissions_repo is not None:
                perms = self._permissions_repo.get(tenant_id, agent.agent_id)
                if perms is not None and not perms.allows_category(product.category):
                    self._audit(
                        AuditEventType.POLICY_EVALUATED,
                        action,
                        tenant_id,
                        actor="auth_service",
                        data={"decision": "DENIED", "category": product.category},
                        trace_id=trace_id,
                    )
                    return PurchaseExecutionResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        rejection_reason=f"Category '{product.category}' is not permitted for agent '{agent.agent_id}'",
                    )

        # Merchant verification (§30, §37, §177 Vector 9)
        merchant = None
        resolved_merchant_id = product.merchant_id if product is not None else None
        if self._merchant_repo is not None and resolved_merchant_id is not None:
            merchant = self._merchant_repo.get(tenant_id, resolved_merchant_id)
            if merchant is None:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="merchant_verifier",
                    data={"decision": "DENIED", "reason": "MERCHANT_NOT_FOUND"},
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=f"Merchant '{resolved_merchant_id}' not found in tenant '{tenant_id}'",
                )
            if not merchant.is_active or merchant.status != MerchantStatus.ACTIVE:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="merchant_verifier",
                    data={"decision": "DENIED", "reason": f"MERCHANT_{merchant.status.value}"},
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=f"Merchant '{resolved_merchant_id}' is {merchant.status.value} (not active)",
                )
            if not merchant.allowed_agent_purchase:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="merchant_verifier",
                    data={"decision": "DENIED", "reason": "AGENT_PURCHASE_NOT_ALLOWED"},
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=f"Merchant '{resolved_merchant_id}' does not allow autonomous agent purchases",
                )
            if (
                merchant.max_order_amount_minor is not None
                and action.amount is not None
                and action.amount.minor_units > merchant.max_order_amount_minor
            ):
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="merchant_verifier",
                    data={
                        "decision": "DENIED",
                        "reason": "MERCHANT_MAX_ORDER_EXCEEDED",
                        "max_order_minor": merchant.max_order_amount_minor,
                    },
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason=f"Order amount exceeds merchant max order limit of {merchant.max_order_amount_minor} minor units",
                )

        # Persist immutable catalog evidence observed during verification (§32)
        self._record_catalog_evidence(
            tenant_id=tenant_id,
            variant=variant,
            product=product,
            inventory=inventory,
            merchant_id=resolved_merchant_id,
        )

        expected_total = None
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
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                conflicts=conflicts,
                rejection_reason="Evidence conflict detected (price/inventory mismatch)",
            )

        # 5. Advisory Decision Intelligence (§28, §177 Vector 5)
        decision_summary = None
        if self._decision_engine is not None:
            try:
                context = DecisionContext(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    agent_id=action.agent_id,
                    amount=action.amount,
                    category=action.resource.type,
                )
                decision_summary = self._decision_engine.evaluate(context)
                jev_data: dict[str, Any] = {
                    "recommendation": decision_summary.recommendation.value,
                    "risk_level": decision_summary.risk_level.value,
                    "confidence": decision_summary.confidence,
                    "model_version": decision_summary.model_version,
                    "prompt_version": decision_summary.prompt_version,
                    "tool_policy_version": decision_summary.tool_policy_version,
                    "policy_version": decision_summary.policy_version,
                }
                self._audit(
                    AuditEventType.JEV_EVALUATED,
                    action,
                    tenant_id,
                    actor=decision_summary.model_provider,
                    data=jev_data,
                    trace_id=trace_id,
                )
            except Exception:
                # Advisory AI provider failure handled gracefully (§2.2, §134)
                # Advisory AI cannot crash execution or override deterministic policy
                logger.exception(
                    "Advisory decision engine failed; continuing with deterministic policy",
                    extra={"action_id": action.action_id, "tenant_id": tenant_id},
                )
                decision_summary = None

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
            request=SpendingPolicyRequest(
                amount=action.amount,
                spent_today_minor=self._get_spent_today_minor(tenant_id, action.agent_id),
            ),
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
            trace_id=trace_id,
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
                merchant_id=(
                    merchant.merchant_id
                    if merchant
                    else (resolved_merchant_id or action.resource.id)
                ),
            )
            self._audit(
                AuditEventType.APPROVAL_REQUESTED,
                action,
                tenant_id,
                actor="system",
                data={"approval_id": approval.approval_id, "amount": str(action.amount)},
                trace_id=trace_id,
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
        resolved_m_id = (
            merchant.merchant_id if merchant else (resolved_merchant_id or action.resource.id)
        )
        return self._execute_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            variant_id=variant_id,
            quantity=quantity,
            decision_summary=decision_summary,
            merchant_id=resolved_m_id,
            idempotency_key=idempotency_key or f"idem_{action.action_id}",
            trace_id=trace_id,
        )

    def _execute_order_and_payment_pipeline(
        self,
        action: ActionRequest,
        tenant_id: str,
        variant_id: str,
        quantity: int,
        decision_summary: Decision | None = None,
        approval: Approval | None = None,
        merchant_id: str | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> PurchaseExecutionResult:
        if action.amount is None:
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason="Amount missing",
            )

        effective_merchant_id = merchant_id or action.resource.id

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
            try:
                payment_attempt = self._payment_provider.create_payment(
                    tenant_id=tenant_id,
                    intent_id=f"intent_{action.action_id}",
                    amount=action.amount,
                    idempotency_key=idem_key,
                )
            except Exception as exc:
                # SAGA COMPENSATION: Release reserved inventory on payment creation failure (§44)
                if self._inventory_repo is not None:
                    try:
                        self._inventory_repo.release(tenant_id, variant_id, quantity)
                    except Exception:
                        logger.exception(
                            "Inventory compensation failed after payment creation failure",
                            extra={
                                "action_id": action.action_id,
                                "tenant_id": tenant_id,
                                "variant_id": variant_id,
                            },
                        )
                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={"reason": f"Payment creation failed: {exc}"},
                    trace_id=trace_id,
                )
                raise

            if self._payment_repo is not None:
                self._payment_repo.save(tenant_id, payment_attempt)

            # Persist provider transaction mapping (§39, §41, §49)
            prov_payment_id = payment_attempt.provider_payment_id or payment_attempt.attempt_id
            mapping = ProviderTransactionMapping(
                mapping_id=f"map_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                attempt_id=payment_attempt.attempt_id,
                provider=payment_attempt.provider,
                provider_payment_id=prov_payment_id,
            )
            self._provider_mapping_repo.save(mapping)
            if (
                payment_attempt.provider_payment_id
                and payment_attempt.provider_payment_id != payment_attempt.attempt_id
            ):
                alt_mapping = ProviderTransactionMapping(
                    mapping_id=f"map_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    attempt_id=payment_attempt.attempt_id,
                    provider=payment_attempt.provider,
                    provider_payment_id=payment_attempt.attempt_id,
                )
                self._provider_mapping_repo.save(alt_mapping)

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
                trace_id=trace_id,
            )

            # Check payment success
            if payment_attempt.status in (PaymentStatus.DECLINED, PaymentStatus.FAILED):
                # SAGA COMPENSATION: Release reserved inventory (§44)
                if self._inventory_repo is not None:
                    self._inventory_repo.release(tenant_id, variant_id, quantity)

                self._audit(
                    AuditEventType.PAYMENT_FAILED,
                    action,
                    tenant_id,
                    actor="payment_provider",
                    data={
                        "payment_id": payment_attempt.attempt_id,
                        "status": payment_attempt.status.value,
                    },
                    trace_id=trace_id,
                )

                cancelled_order = Order(
                    order_id=f"ord_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    merchant_id=effective_merchant_id,
                    buyer_id=action.principal_id,
                    agent_id=action.agent_id,
                    variant_id=variant_id,
                    quantity=quantity,
                    total_amount=action.amount,
                    status=OrderStatus.CANCELLED,
                    payment_attempt_id=payment_attempt.attempt_id,
                    cancellation_reason="Payment declined",
                )
                if self._order_repo is not None:
                    self._order_repo.save(tenant_id, cancelled_order)

                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={"order_id": cancelled_order.order_id, "reason": "Payment declined"},
                    trace_id=trace_id,
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
                                "decision": PolicyDecision.ALLOW.value,
                                "amount_minor": action.amount.minor_units,
                                "currency": action.amount.currency,
                                "action_id": action.action_id,
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
                    trace_id=trace_id,
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
                                "decision": PolicyDecision.ALLOW.value,
                                "amount_minor": action.amount.minor_units,
                                "currency": action.amount.currency,
                                "action_id": action.action_id,
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
            merchant_id=effective_merchant_id,
            buyer_id=action.principal_id,
            agent_id=action.agent_id,
            variant_id=variant_id,
            quantity=quantity,
            total_amount=action.amount,
            status=OrderStatus.CONFIRMED,
            payment_attempt_id=payment_attempt.attempt_id if payment_attempt else None,
        )

        if self._order_repo is not None:
            try:
                self._order_repo.save(tenant_id, order)
            except Exception as exc:
                # SAGA COMPENSATION: Trigger refund if payment was captured and release inventory (§44)
                if (
                    self._payment_provider is not None
                    and payment_attempt is not None
                    and payment_attempt.status is PaymentStatus.CAPTURED
                ):
                    try:
                        self._payment_provider.refund(
                            tenant_id=tenant_id,
                            attempt_id=payment_attempt.attempt_id,
                            amount=action.amount,
                            reason="Order persistence failure compensation",
                        )
                    except Exception:
                        logger.exception(
                            "Payment refund compensation failed after order persistence failure",
                            extra={
                                "action_id": action.action_id,
                                "tenant_id": tenant_id,
                                "attempt_id": payment_attempt.attempt_id,
                            },
                        )
                if self._inventory_repo is not None:
                    self._inventory_repo.release(tenant_id, variant_id, quantity)
                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={
                        "order_id": order.order_id,
                        "reason": f"Order persistence failed after payment capture: {exc}",
                    },
                    trace_id=trace_id,
                )
                raise

        self._audit(
            AuditEventType.ORDER_CREATED,
            action,
            tenant_id,
            actor="order_service",
            data={"order_id": order.order_id, "status": order.status.value},
            trace_id=trace_id,
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
                        "decision": PolicyDecision.ALLOW.value,
                        "amount_minor": action.amount.minor_units,
                        "currency": action.amount.currency,
                        "action_id": action.action_id,
                    },
                ),
            )

        # Record the completed purchase against the agent's daily spend ledger so the
        # daily spending limit is enforced across requests (§18, §19).
        self._record_daily_spend(tenant_id, action.agent_id, action.amount.minor_units)

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
        trace_id: str | None = None,
    ) -> PurchaseExecutionResult:
        """Execute a purchase that was previously approved by a human (§20)."""
        if self._approval_repo is None:
            raise InvalidCommerceError("Approval repository not configured")

        # 0. Idempotency Check BEFORE state mutation (§42, B-IDEM-01, B-IDEM-02)
        if idempotency_key is not None and self._idempotency_store is not None:
            existing = self._idempotency_store.get(tenant_id, idempotency_key)
            if existing is not None:
                # Check for payload / amount / currency conflict (§42, B-IDEM-02)
                expected_amount_minor = existing.result_data.get("amount_minor")
                if (
                    expected_amount_minor is not None
                    and action.amount is not None
                    and action.amount.minor_units != expected_amount_minor
                ):
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored amount {expected_amount_minor} != incoming amount {action.amount.minor_units}"
                    )
                expected_currency = existing.result_data.get("currency")
                if (
                    expected_currency is not None
                    and action.amount is not None
                    and action.amount.currency != expected_currency
                ):
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored currency '{expected_currency}' != incoming currency '{action.amount.currency}'"
                    )
                stored_action_id = existing.result_data.get("action_id", existing.request_hash)
                if stored_action_id and stored_action_id != action.action_id:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' conflict: "
                        f"stored action '{stored_action_id}' != incoming action '{action.action_id}'"
                    )

                cached_order = None
                order_id = existing.result_data.get("order_id")
                if order_id and self._order_repo is not None:
                    cached_order = self._order_repo.get(tenant_id, order_id)
                cached_payment = None
                payment_id = existing.result_data.get("payment_id")
                if payment_id and self._payment_repo is not None:
                    cached_payment = self._payment_repo.get(tenant_id, payment_id)
                status_raw = existing.result_data.get("status", "COMPLETED")
                try:
                    cached_status = ActionStatus(status_raw)
                except ValueError:
                    cached_status = ActionStatus.COMPLETED
                decision_raw = existing.result_data.get("decision", "ALLOW")
                try:
                    cached_decision = PolicyDecision(decision_raw)
                except ValueError:
                    cached_decision = PolicyDecision.ALLOW
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=cached_status,
                    decision=cached_decision,
                    order=cached_order,
                    payment=cached_payment,
                )

        # 1. Pre-verify binding if approval exists, preventing unauthorized consumption burn on mismatch (§20)
        resolved_merchant_id = self._resolve_action_merchant_id(tenant_id, action, variant_id)
        if self._approval_repo is not None:
            pre_appr = self._approval_repo.get(tenant_id, approval_id)
            if pre_appr is not None and pre_appr.is_approved:
                self._approval_service.verify_binding(
                    pre_appr, action, merchant_id=resolved_merchant_id
                )

        # 2. Atomic check-and-consume or lock-guarded consumption to prevent TOCTOU race (§20, §177 Vector 10)
        with self._approval_lock:
            if hasattr(self._approval_repo, "atomic_consume"):
                consumed_approval = self._approval_repo.atomic_consume(tenant_id, approval_id)
            else:
                approval = self._approval_repo.get(tenant_id, approval_id)
                if approval is None:
                    raise InvalidCommerceError(
                        f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
                    )
                if approval.status == ApprovalStatus.CONSUMED:
                    raise ApprovalAlreadyConsumedError(
                        f"Approval '{approval_id}' has already been consumed"
                    )
                if not approval.is_approved:
                    raise ApprovalBindingMismatchError(
                        f"Approval is in status '{approval.status.value}', not APPROVED"
                    )
                consumed_approval = approval.consume()
                self._approval_repo.save(tenant_id, consumed_approval)

        # 3. Strict binding verification on consumed approval (§0.4, §20)
        self._approval_service.verify_binding(
            consumed_approval, action, merchant_id=resolved_merchant_id
        )

        # 3. Agent Identity & Lifecycle validation (§14, §15, §16, §177 Vectors 1 & 4, B-AUTH-02)
        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, action.agent_id)

        if agent is None:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={
                    "decision": "DENIED",
                    "reason": "UNREGISTERED_AGENT",
                    "agent_id": action.agent_id,
                },
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Agent '{action.agent_id}' not found in tenant '{tenant_id}'",
            )

        try:
            self._auth_service.authorize_action(agent, action, tenant_id)
        except DomainError as err:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={"decision": "DENIED", "error": str(err)},
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Authorization rejected: {err}",
            )

        # 4. Commerce & Evidence Verification & Drift Detection (§30, §32, §33, §177 Vectors 8 & 12, B-DRIFT-01)
        variant = None
        if self._variant_repo is not None:
            variant = self._variant_repo.get(tenant_id, variant_id)

        if variant is None:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="commerce_service",
                data={"decision": "DENIED", "reason": "VARIANT_NOT_FOUND"},
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason="Variant not found",
            )

        product = None
        if self._product_repo is not None:
            product = self._product_repo.get(tenant_id, variant.product_id)
            if product is None:
                self._audit(
                    AuditEventType.POLICY_EVALUATED,
                    action,
                    tenant_id,
                    actor="commerce_service",
                    data={"decision": "DENIED", "reason": "PRODUCT_NOT_FOUND"},
                    trace_id=trace_id,
                )
                return PurchaseExecutionResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.DENY,
                    rejection_reason="Product not found",
                )
            if self._permissions_repo is not None:
                perms = self._permissions_repo.get(tenant_id, agent.agent_id)
                if perms is not None and not perms.allows_category(product.category):
                    self._audit(
                        AuditEventType.POLICY_EVALUATED,
                        action,
                        tenant_id,
                        actor="auth_service",
                        data={"decision": "DENIED", "category": product.category},
                        trace_id=trace_id,
                    )
                    return PurchaseExecutionResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        rejection_reason=f"Category '{product.category}' is not permitted for agent '{agent.agent_id}'",
                    )

        inventory = None
        if self._inventory_repo is not None:
            inventory = self._inventory_repo.get(tenant_id, variant_id)

        expected_total = None
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
                trace_id=trace_id,
            )
            return PurchaseExecutionResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                conflicts=conflicts,
                rejection_reason="Evidence conflict detected (price/inventory mismatch)",
            )

        # 5. Merchant verification for approved purchase (§2.4, §20, §30, §177 Vector 9)
        target_merchant_id = consumed_approval.binding.merchant_id
        if self._merchant_repo is not None:
            merchant = None
            if target_merchant_id:
                merchant = self._merchant_repo.get(tenant_id, target_merchant_id)
            if merchant is None and product is not None and product.merchant_id:
                merchant = self._merchant_repo.get(tenant_id, product.merchant_id)
            elif merchant is None and self._product_repo is not None:
                p = (
                    self._product_repo.get(tenant_id, target_merchant_id)
                    if target_merchant_id
                    else None
                )
                if p is None and self._variant_repo is not None:
                    v = self._variant_repo.get(tenant_id, variant_id)
                    if v is not None:
                        p = self._product_repo.get(tenant_id, v.product_id)
                if p is not None and p.merchant_id:
                    merchant = self._merchant_repo.get(tenant_id, p.merchant_id)

            if target_merchant_id or merchant is not None:
                if (
                    merchant is None
                    or not merchant.is_active
                    or merchant.status != MerchantStatus.ACTIVE
                ):
                    self._audit(
                        AuditEventType.POLICY_EVALUATED,
                        action,
                        tenant_id,
                        actor="merchant_verifier",
                        data={
                            "decision": "DENIED",
                            "reason": f"MERCHANT_{merchant.status.value}"
                            if merchant
                            else "MERCHANT_NOT_FOUND",
                        },
                        trace_id=trace_id,
                    )
                    return PurchaseExecutionResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        rejection_reason=f"Merchant '{target_merchant_id}' is not active",
                    )
                if not merchant.allowed_agent_purchase:
                    self._audit(
                        AuditEventType.POLICY_EVALUATED,
                        action,
                        tenant_id,
                        actor="merchant_verifier",
                        data={"decision": "DENIED", "reason": "AGENT_PURCHASE_NOT_ALLOWED"},
                        trace_id=trace_id,
                    )
                    return PurchaseExecutionResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        rejection_reason=f"Merchant '{target_merchant_id}' does not allow autonomous agent purchases",
                    )
                if (
                    merchant.max_order_amount_minor is not None
                    and action.amount is not None
                    and action.amount.minor_units > merchant.max_order_amount_minor
                ):
                    self._audit(
                        AuditEventType.POLICY_EVALUATED,
                        action,
                        tenant_id,
                        actor="merchant_verifier",
                        data={
                            "decision": "DENIED",
                            "reason": "MERCHANT_MAX_ORDER_EXCEEDED",
                            "max_order_minor": merchant.max_order_amount_minor,
                        },
                        trace_id=trace_id,
                    )
                    return PurchaseExecutionResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        rejection_reason=f"Order amount exceeds merchant max order limit of {merchant.max_order_amount_minor} minor units",
                    )

        # Persist immutable catalog evidence observed during re-verification (§32)
        self._record_catalog_evidence(
            tenant_id=tenant_id,
            variant=variant,
            product=product,
            inventory=inventory,
            merchant_id=consumed_approval.binding.merchant_id
            or (product.merchant_id if product is not None else None),
        )

        self._audit(
            AuditEventType.APPROVAL_GRANTED,
            action,
            tenant_id,
            actor=consumed_approval.approver_id or "human_approver",
            data={"approval_id": approval_id},
            trace_id=trace_id,
        )

        return self._execute_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            variant_id=variant_id,
            quantity=quantity,
            approval=consumed_approval,
            merchant_id=consumed_approval.binding.merchant_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    # ------------------------------------------------------------------
    # Multi-item cart (§38, ADR-0006)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_cart_action(
        action_id: str,
        action: str,
        tenant_id: str,
        principal_id: str,
        agent_id: str,
        cart_id: str,
        amount: Money | None = None,
    ) -> ActionRequest:
        """Build the canonical ActionRequest for a cart operation (ADR-0006)."""
        return ActionRequest(
            action_id=action_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            agent_id=agent_id,
            domain="commerce",
            action=action,
            resource=ResourceRef(type="cart", id=cart_id),
            requested_at=datetime.now(UTC),
            intent=Intent(purpose="cart"),
            amount=amount,
        )

    def _load_cart_or_fail(self, tenant_id: str, cart_id: str) -> Cart:
        if self._cart_repo is None:
            raise InvalidCommerceError("Cart repository not configured")
        cart = self._cart_repo.get(tenant_id, cart_id)
        if cart is None:
            raise InvalidCommerceError(f"Cart '{cart_id}' not found in tenant '{tenant_id}'")
        return cart

    def create_cart(
        self,
        tenant_id: str,
        agent_id: str,
        principal_id: str,
        trace_id: str | None = None,
    ) -> Cart:
        """Create an empty single-merchant cart for an authorized agent (ADR-0006)."""
        if self._cart_repo is None:
            raise InvalidCommerceError("Cart repository not configured")

        cart_id = f"cart_{uuid.uuid4().hex[:16]}"
        action = self._build_cart_action(
            action_id=f"act_{uuid.uuid4().hex[:12]}",
            action="cart",
            tenant_id=tenant_id,
            principal_id=principal_id,
            agent_id=agent_id,
            cart_id=cart_id,
        )

        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, agent_id)
        if agent is None:
            raise InvalidIdentityError(f"Agent '{agent_id}' not found in tenant '{tenant_id}'")

        self._auth_service.authorize_action(agent, action, tenant_id)

        cart = Cart(
            cart_id=cart_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            agent_id=agent_id,
        )
        self._cart_repo.save(tenant_id, cart)
        self._audit(
            AuditEventType.ACTION_REQUESTED,
            action,
            tenant_id,
            actor=agent_id,
            data={"action": "cart_create", "cart_id": cart_id},
            trace_id=trace_id,
        )
        return cart

    def add_cart_item(
        self,
        tenant_id: str,
        cart_id: str,
        variant_id: str,
        quantity: int = 1,
        trace_id: str | None = None,
    ) -> Cart:
        """Add (or merge) a line item on an ACTIVE cart (ADR-0006).

        The variant's merchant must match the merchant already bound to the cart;
        mixing merchants fails closed.
        """
        if self._cart_repo is None:
            raise InvalidCommerceError("Cart repository not configured")

        cart = self._load_cart_or_fail(tenant_id, cart_id)
        action = self._build_cart_action(
            action_id=f"act_{uuid.uuid4().hex[:12]}",
            action="cart",
            tenant_id=tenant_id,
            principal_id=cart.principal_id,
            agent_id=cart.agent_id,
            cart_id=cart_id,
        )

        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, cart.agent_id)
        if agent is None:
            raise InvalidIdentityError(f"Agent '{cart.agent_id}' not found in tenant '{tenant_id}'")
        self._auth_service.authorize_action(agent, action, tenant_id)

        variant = (
            self._variant_repo.get(tenant_id, variant_id)
            if self._variant_repo is not None
            else None
        )
        if variant is None:
            raise InvalidCommerceError(f"Variant '{variant_id}' not found in tenant '{tenant_id}'")
        product = (
            self._product_repo.get(tenant_id, variant.product_id)
            if self._product_repo is not None
            else None
        )
        if product is None:
            raise InvalidCommerceError(
                f"Product '{variant.product_id}' not found in tenant '{tenant_id}'"
            )

        updated = cart.add_item(
            variant_id=variant_id,
            quantity=quantity,
            merchant_id=product.merchant_id,
        )
        self._cart_repo.save(tenant_id, updated)
        self._audit(
            AuditEventType.ACTION_REQUESTED,
            action,
            tenant_id,
            actor=cart.agent_id,
            data={
                "action": "cart_add_item",
                "cart_id": cart_id,
                "variant_id": variant_id,
                "quantity": quantity,
            },
            trace_id=trace_id,
        )
        return updated

    def remove_cart_item(
        self,
        tenant_id: str,
        cart_id: str,
        variant_id: str,
        trace_id: str | None = None,
    ) -> Cart:
        """Remove a line item from an ACTIVE cart (ADR-0006)."""
        if self._cart_repo is None:
            raise InvalidCommerceError("Cart repository not configured")

        cart = self._load_cart_or_fail(tenant_id, cart_id)
        action = self._build_cart_action(
            action_id=f"act_{uuid.uuid4().hex[:12]}",
            action="cart",
            tenant_id=tenant_id,
            principal_id=cart.principal_id,
            agent_id=cart.agent_id,
            cart_id=cart_id,
        )

        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, cart.agent_id)
        if agent is None:
            raise InvalidIdentityError(f"Agent '{cart.agent_id}' not found in tenant '{tenant_id}'")
        self._auth_service.authorize_action(agent, action, tenant_id)

        updated = cart.remove_item(variant_id)
        self._cart_repo.save(tenant_id, updated)
        self._audit(
            AuditEventType.ACTION_REQUESTED,
            action,
            tenant_id,
            actor=cart.agent_id,
            data={"action": "cart_remove_item", "cart_id": cart_id, "variant_id": variant_id},
            trace_id=trace_id,
        )
        return updated

    def get_cart_payload(self, tenant_id: str, cart_id: str) -> dict[str, Any] | None:
        """Return a read-only view of a cart with live prices and availability (ADR-0006)."""
        if self._cart_repo is None:
            return None
        cart = self._cart_repo.get(tenant_id, cart_id)
        if cart is None:
            return None

        items_payload: list[dict[str, Any]] = []
        line_amounts: list[Money] = []
        all_resolvable = True
        for item in cart.items:
            variant = (
                self._variant_repo.get(tenant_id, item.variant_id)
                if self._variant_repo is not None
                else None
            )
            product = (
                self._product_repo.get(tenant_id, variant.product_id)
                if variant is not None and self._product_repo is not None
                else None
            )
            inventory = (
                self._inventory_repo.get(tenant_id, item.variant_id)
                if self._inventory_repo is not None
                else None
            )
            line_total = variant.price.multiply(item.quantity) if variant is not None else None
            if line_total is not None:
                line_amounts.append(line_total)
            else:
                all_resolvable = False
            items_payload.append(
                {
                    "variant_id": item.variant_id,
                    "quantity": item.quantity,
                    "product_id": product.product_id if product is not None else None,
                    "name": variant.name if variant is not None else None,
                    "sku": variant.sku if variant is not None else None,
                    "unit_price_minor": variant.price.minor_units if variant is not None else None,
                    "currency": variant.price.currency if variant is not None else None,
                    "available": inventory.effective_available if inventory is not None else 0,
                    "line_total_minor": line_total.minor_units if line_total is not None else None,
                    "resolved": variant is not None and product is not None,
                }
            )

        total_minor: int | None = None
        total_currency: str | None = None
        if all_resolvable and line_amounts:
            try:
                total = total_money(tuple(line_amounts), currency=line_amounts[0].currency)
                total_minor = total.minor_units
                total_currency = total.currency
            except CurrencyMismatchError:
                total_minor = None
                total_currency = None

        return {
            "cart_id": cart.cart_id,
            "tenant_id": cart.tenant_id,
            "principal_id": cart.principal_id,
            "agent_id": cart.agent_id,
            "merchant_id": cart.merchant_id,
            "status": cart.status.value,
            "items": items_payload,
            "total_minor": total_minor,
            "currency": total_currency,
            "created_at": cart.created_at.isoformat(),
            "updated_at": cart.updated_at.isoformat(),
        }

    def _resolve_cart_lines(
        self, tenant_id: str, cart: Cart
    ) -> tuple[list[tuple[CartItem, ProductVariant, Product, Inventory | None]], str | None]:
        """Resolve every cart item to its live variant, product, and inventory (ADR-0006).

        Returns (lines, rejection_reason); rejection_reason is set when any line cannot
        be resolved and checkout must fail closed.
        """
        lines: list[tuple[CartItem, ProductVariant, Product, Inventory | None]] = []
        for item in cart.items:
            variant = (
                self._variant_repo.get(tenant_id, item.variant_id)
                if self._variant_repo is not None
                else None
            )
            if variant is None:
                return [], f"Variant '{item.variant_id}' not found"
            product = (
                self._product_repo.get(tenant_id, variant.product_id)
                if self._product_repo is not None
                else None
            )
            if product is None:
                return [], f"Product '{variant.product_id}' not found"
            inventory = (
                self._inventory_repo.get(tenant_id, item.variant_id)
                if self._inventory_repo is not None
                else None
            )
            lines.append((item, variant, product, inventory))
        return lines, None

    def _cart_total_or_none(
        self, lines: list[tuple[CartItem, ProductVariant, Product, Inventory | None]]
    ) -> tuple[Money | None, str | None]:
        """Compute the deterministic cart total, or a rejection reason if currencies differ."""
        line_amounts = [
            variant.price.multiply(item.quantity) for item, variant, _product, _inv in lines
        ]
        if not line_amounts:
            return None, "Cart is empty"
        try:
            total = total_money(tuple(line_amounts), currency=line_amounts[0].currency)
        except CurrencyMismatchError as err:
            return None, f"Cart contains mixed currencies: {err}"
        return total, None

    def _cart_idempotency_check(
        self, action: ActionRequest, tenant_id: str, idempotency_key: str | None
    ) -> CartCheckoutResult | None:
        """Return the stored result for a replayed checkout, or None (§42)."""
        if self._idempotency_store is None or idempotency_key is None:
            return None
        existing = self._idempotency_store.get(tenant_id, idempotency_key)
        if existing is None:
            return None

        expected_amount_minor = existing.result_data.get("amount_minor")
        if (
            expected_amount_minor is not None
            and action.amount is not None
            and action.amount.minor_units != expected_amount_minor
        ):
            raise IdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' conflict: "
                f"stored amount {expected_amount_minor} != incoming amount {action.amount.minor_units}"
            )
        expected_currency = existing.result_data.get("currency")
        if (
            expected_currency is not None
            and action.amount is not None
            and action.amount.currency != expected_currency
        ):
            raise IdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' conflict: "
                f"stored currency '{expected_currency}' != incoming currency '{action.amount.currency}'"
            )
        stored_action_id = existing.result_data.get("action_id", existing.request_hash)
        if stored_action_id and stored_action_id != action.action_id:
            raise IdempotencyConflictError(
                f"Idempotency key '{idempotency_key}' conflict: "
                f"stored action '{stored_action_id}' != incoming action '{action.action_id}'"
            )

        cached_cart = None
        cart_id = existing.result_data.get("cart_id")
        if cart_id and self._cart_repo is not None:
            cached_cart = self._cart_repo.get(tenant_id, str(cart_id))
        cached_orders: list[Order] = []
        raw_order_ids = existing.result_data.get("order_ids")
        if isinstance(raw_order_ids, list) and self._order_repo is not None:
            for raw_order_id in raw_order_ids:
                order = self._order_repo.get(tenant_id, str(raw_order_id))
                if order is not None:
                    cached_orders.append(order)
        cached_payment = None
        payment_id = existing.result_data.get("payment_id")
        if payment_id and self._payment_repo is not None:
            cached_payment = self._payment_repo.get(tenant_id, str(payment_id))
        status_raw = existing.result_data.get("status", "COMPLETED")
        try:
            cached_status = ActionStatus(status_raw)
        except ValueError:
            cached_status = ActionStatus.COMPLETED
        decision_raw = existing.result_data.get("decision", "ALLOW")
        try:
            cached_decision = PolicyDecision(decision_raw)
        except ValueError:
            cached_decision = PolicyDecision.ALLOW
        return CartCheckoutResult(
            action_id=action.action_id,
            tenant_id=tenant_id,
            status=cached_status,
            decision=cached_decision,
            cart=cached_cart,
            orders=tuple(cached_orders),
            payment=cached_payment,
        )

    def execute_cart_purchase(
        self,
        action: ActionRequest,
        tenant_id: str,
        cart_id: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CartCheckoutResult:
        """Execute the canonical multi-item cart checkout flow (ADR-0006).

        Serialized per (tenant, cart): two concurrent checkouts of the same ACTIVE
        cart can never both reach the payment pipeline; the loser observes
        CHECKED_OUT and fails closed (§43, §177 duplicate-payment defense).
        """
        with self._cart_checkout_lock(tenant_id, cart_id):
            return self._execute_cart_purchase_locked(
                action=action,
                tenant_id=tenant_id,
                cart_id=cart_id,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
            )

    def _execute_cart_purchase_locked(
        self,
        action: ActionRequest,
        tenant_id: str,
        cart_id: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CartCheckoutResult:
        """Cart checkout body; caller must hold the cart checkout lock (ADR-0006)."""
        if action.tenant_id != tenant_id:
            raise CrossTenantAccessError("ActionRequest tenant does not match context")

        replay = self._cart_idempotency_check(action, tenant_id, idempotency_key)
        if replay is not None:
            return replay

        self._audit(
            AuditEventType.ACTION_REQUESTED,
            action,
            tenant_id,
            actor=action.agent_id,
            data={"action": action.action, "cart_id": cart_id, "amount": str(action.amount)},
            trace_id=trace_id,
        )

        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, action.agent_id)
        if agent is None:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={
                    "decision": "DENIED",
                    "reason": "UNREGISTERED_AGENT",
                    "agent_id": action.agent_id,
                },
                trace_id=trace_id,
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Agent '{action.agent_id}' not found in tenant '{tenant_id}'",
            )

        try:
            self._auth_service.authorize_action(agent, action, tenant_id)
        except DomainError as err:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="auth_service",
                data={"decision": "DENIED", "error": str(err)},
                trace_id=trace_id,
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Authorization rejected: {err}",
            )

        cart = self._cart_repo.get(tenant_id, cart_id) if self._cart_repo is not None else None
        if cart is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Cart '{cart_id}' not found",
            )
        if cart.status is not CartStatus.ACTIVE:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=f"Cart is in status '{cart.status.value}', not ACTIVE",
            )
        if not cart.items:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason="Cart is empty",
            )

        lines, rejection = self._resolve_cart_lines(tenant_id, cart)
        if rejection is not None:
            self._audit(
                AuditEventType.POLICY_EVALUATED,
                action,
                tenant_id,
                actor="commerce_service",
                data={"decision": "DENIED", "reason": rejection},
                trace_id=trace_id,
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=rejection,
            )

        merchant_id = cart.merchant_id or lines[0][2].merchant_id
        if any(line_product.merchant_id != merchant_id for _i, _v, line_product, _inv in lines):
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason="Cart contains items from multiple merchants",
            )

        if self._permissions_repo is not None:
            perms = self._permissions_repo.get(tenant_id, agent.agent_id)
            if perms is not None:
                for _item, _variant, line_product, _inv in lines:
                    if not perms.allows_category(line_product.category):
                        self._audit(
                            AuditEventType.POLICY_EVALUATED,
                            action,
                            tenant_id,
                            actor="auth_service",
                            data={"decision": "DENIED", "category": line_product.category},
                            trace_id=trace_id,
                        )
                        return CartCheckoutResult(
                            action_id=action.action_id,
                            tenant_id=tenant_id,
                            status=ActionStatus.FAILED,
                            decision=PolicyDecision.DENY,
                            cart=cart,
                            rejection_reason=(
                                f"Category '{line_product.category}' is not permitted for "
                                f"agent '{agent.agent_id}'"
                            ),
                        )

        total, total_rejection = self._cart_total_or_none(lines)
        if total_rejection is not None or total is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=total_rejection or "Cart total could not be computed",
            )

        merchant_verification = self._verify_cart_merchant(
            tenant_id, action, merchant_id, total, trace_id
        )
        if merchant_verification is not None:
            merchant_verification = replace(merchant_verification, cart=cart)
            return merchant_verification

        for _item, variant, line_product, inventory in lines:
            self._record_catalog_evidence(
                tenant_id=tenant_id,
                variant=variant,
                product=line_product,
                inventory=inventory,
                merchant_id=merchant_id,
            )

        conflicts = self._detect_cart_conflicts(tenant_id, action, cart, lines, total)
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
                trace_id=trace_id,
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                conflicts=conflicts,
                rejection_reason="Evidence conflict detected (price/inventory mismatch)",
            )

        decision_summary = self._evaluate_cart_decision(action, tenant_id, total, trace_id)

        if action.amount is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason="Action has no amount specified",
            )

        limits = SpendingLimits(
            per_transaction_minor=200000,
            daily_minor=500000,
            requires_approval_above_minor=200000,
        )
        if self._permissions_repo is not None and agent is not None:
            perms = self._permissions_repo.get(tenant_id, agent.agent_id)
            if perms is not None:
                limits = perms.limits

        policy_result = self._policy_engine.evaluate(
            request=SpendingPolicyRequest(
                amount=action.amount,
                spent_today_minor=self._get_spent_today_minor(tenant_id, action.agent_id),
            ),
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
                "cart_id": cart_id,
            },
            trace_id=trace_id,
        )

        if policy_result.decision is PolicyDecision.DENY:
            reason_msg = (
                policy_result.reasons[0].message
                if policy_result.reasons
                else "Policy limit exceeded"
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                decision_summary=decision_summary,
                rejection_reason=reason_msg,
            )

        if policy_result.decision is PolicyDecision.REVIEW:
            approval = self._approval_service.create_approval(
                action=action,
                tenant_id=tenant_id,
                reason="Cart total exceeds autonomous threshold; awaiting human review",
                merchant_id=merchant_id,
            )
            self._audit(
                AuditEventType.APPROVAL_REQUESTED,
                action,
                tenant_id,
                actor="system",
                data={
                    "approval_id": approval.approval_id,
                    "cart_id": cart_id,
                    "amount": str(action.amount),
                },
                trace_id=trace_id,
            )
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.AWAITING_APPROVAL,
                decision=PolicyDecision.REVIEW,
                cart=cart,
                approval=approval,
                decision_summary=decision_summary,
            )

        return self._execute_cart_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            cart=cart,
            lines=lines,
            merchant_id=merchant_id,
            decision_summary=decision_summary,
            idempotency_key=idempotency_key or f"idem_{action.action_id}",
            trace_id=trace_id,
        )

    def _verify_cart_merchant(
        self,
        tenant_id: str,
        action: ActionRequest,
        merchant_id: str,
        total: Money,
        trace_id: str | None,
    ) -> CartCheckoutResult | None:
        """Verify merchant lifecycle and limits for the cart total (§37, §177 Vector 9)."""
        if self._merchant_repo is None:
            return None
        merchant = self._merchant_repo.get(tenant_id, merchant_id)
        if merchant is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Merchant '{merchant_id}' not found in tenant '{tenant_id}'",
            )
        if not merchant.is_active or merchant.status != MerchantStatus.ACTIVE:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Merchant '{merchant_id}' is {merchant.status.value} (not active)",
            )
        if not merchant.allowed_agent_purchase:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=(
                    f"Merchant '{merchant_id}' does not allow autonomous agent purchases"
                ),
            )
        if (
            merchant.max_order_amount_minor is not None
            and total.minor_units > merchant.max_order_amount_minor
        ):
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=(
                    f"Cart total exceeds merchant max order limit of "
                    f"{merchant.max_order_amount_minor} minor units"
                ),
            )
        return None

    def _detect_cart_conflicts(
        self,
        tenant_id: str,
        action: ActionRequest,
        cart: Cart,
        lines: list[tuple[CartItem, ProductVariant, Product, Inventory | None]],
        total: Money,
    ) -> tuple[EvidenceConflict, ...]:
        """Detect price and inventory conflicts for a cart checkout (§33)."""
        conflicts: list[EvidenceConflict] = list(
            EvidenceConflictDetector.detect_all_conflicts(
                tenant_id=tenant_id,
                resource_id=cart.cart_id,
                asserted_price=action.amount,
                merchant_price=total,
            )
        )
        for item, _variant, _product, inventory in lines:
            inventory_conflict = EvidenceConflictDetector.detect_inventory_conflict(
                asserted_available=None,
                actual_available=inventory.effective_available if inventory is not None else 0,
                requested_qty=item.quantity,
                tenant_id=tenant_id,
                resource_id=item.variant_id,
            )
            if inventory_conflict is not None:
                conflicts.append(inventory_conflict)
        return tuple(conflicts)

    def _evaluate_cart_decision(
        self,
        action: ActionRequest,
        tenant_id: str,
        total: Money,
        trace_id: str | None,
    ) -> Decision | None:
        """Run the advisory decision engine on the cart total (§28, §177 Vector 5)."""
        if self._decision_engine is None:
            return None
        try:
            context = DecisionContext(
                action_id=action.action_id,
                tenant_id=tenant_id,
                agent_id=action.agent_id,
                amount=total,
                category="cart",
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
                    "model_version": decision_summary.model_version,
                    "prompt_version": decision_summary.prompt_version,
                    "tool_policy_version": decision_summary.tool_policy_version,
                    "policy_version": decision_summary.policy_version,
                },
                trace_id=trace_id,
            )
            return decision_summary
        except Exception:
            logger.exception(
                "Advisory decision engine failed; continuing with deterministic policy",
                extra={"action_id": action.action_id, "tenant_id": tenant_id},
            )
            return None

    def execute_approved_cart_purchase(
        self,
        approval_id: str,
        action: ActionRequest,
        tenant_id: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CartCheckoutResult:
        """Execute a cart checkout previously approved by a human (§20, ADR-0006).

        Shares the per-(tenant, cart) checkout lock with execute_cart_purchase so an
        approved execution and an autonomous attempt on the same cart cannot race
        into duplicate payments (§43, §177).
        """
        with self._cart_checkout_lock(tenant_id, action.resource.id):
            return self._execute_approved_cart_purchase_locked(
                approval_id=approval_id,
                action=action,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                trace_id=trace_id,
            )

    def _execute_approved_cart_purchase_locked(
        self,
        approval_id: str,
        action: ActionRequest,
        tenant_id: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CartCheckoutResult:
        """Approved checkout body; caller must hold the cart checkout lock (ADR-0006)."""
        if self._approval_repo is None:
            raise InvalidCommerceError("Approval repository not configured")

        replay = self._cart_idempotency_check(action, tenant_id, idempotency_key)
        if replay is not None:
            return replay

        cart_id = action.resource.id
        cart = self._cart_repo.get(tenant_id, cart_id) if self._cart_repo is not None else None
        resolved_merchant_id = cart.merchant_id if cart is not None else None

        pre_appr = self._approval_repo.get(tenant_id, approval_id)
        if pre_appr is not None and pre_appr.is_approved:
            self._approval_service.verify_binding(
                pre_appr, action, merchant_id=resolved_merchant_id
            )

        with self._approval_lock:
            if hasattr(self._approval_repo, "atomic_consume"):
                consumed_approval = self._approval_repo.atomic_consume(tenant_id, approval_id)
            else:
                approval = self._approval_repo.get(tenant_id, approval_id)
                if approval is None:
                    raise InvalidCommerceError(
                        f"Approval '{approval_id}' not found in tenant '{tenant_id}'"
                    )
                if approval.status == ApprovalStatus.CONSUMED:
                    raise ApprovalAlreadyConsumedError(
                        f"Approval '{approval_id}' has already been consumed"
                    )
                if not approval.is_approved:
                    raise ApprovalBindingMismatchError(
                        f"Approval is in status '{approval.status.value}', not APPROVED"
                    )
                consumed_approval = approval.consume()
                self._approval_repo.save(tenant_id, consumed_approval)

        self._approval_service.verify_binding(
            consumed_approval, action, merchant_id=resolved_merchant_id
        )

        agent = None
        if self._agent_repo is not None:
            agent = self._agent_repo.get(tenant_id, action.agent_id)
        if agent is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Agent '{action.agent_id}' not found in tenant '{tenant_id}'",
            )
        try:
            self._auth_service.authorize_action(agent, action, tenant_id)
        except DomainError as err:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Authorization rejected: {err}",
            )

        if cart is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                rejection_reason=f"Cart '{cart_id}' not found",
            )
        if cart.status is not CartStatus.ACTIVE:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=f"Cart is in status '{cart.status.value}', not ACTIVE",
            )
        if not cart.items:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason="Cart is empty",
            )

        lines, rejection = self._resolve_cart_lines(tenant_id, cart)
        if rejection is not None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=rejection,
            )

        merchant_id = cart.merchant_id or lines[0][2].merchant_id
        total, total_rejection = self._cart_total_or_none(lines)
        if total_rejection is not None or total is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason=total_rejection or "Cart total could not be computed",
            )

        conflicts = self._detect_cart_conflicts(tenant_id, action, cart, lines, total)
        if conflicts:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                conflicts=conflicts,
                rejection_reason="Evidence conflict detected (price/inventory mismatch)",
            )

        merchant_verification = self._verify_cart_merchant(
            tenant_id, action, merchant_id, total, trace_id
        )
        if merchant_verification is not None:
            merchant_verification = replace(merchant_verification, cart=cart)
            return merchant_verification

        for _item, variant, line_product, inventory in lines:
            self._record_catalog_evidence(
                tenant_id=tenant_id,
                variant=variant,
                product=line_product,
                inventory=inventory,
                merchant_id=merchant_id,
            )

        self._audit(
            AuditEventType.APPROVAL_GRANTED,
            action,
            tenant_id,
            actor=consumed_approval.approver_id or "human_approver",
            data={"approval_id": approval_id, "cart_id": cart_id},
            trace_id=trace_id,
        )

        return self._execute_cart_order_and_payment_pipeline(
            action=action,
            tenant_id=tenant_id,
            cart=cart,
            lines=lines,
            merchant_id=merchant_id,
            approval=consumed_approval,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    def _release_cart_reservations(
        self,
        tenant_id: str,
        lines: list[tuple[CartItem, ProductVariant, Product, Inventory | None]],
        action: ActionRequest,
    ) -> None:
        """Compensate all reserved cart lines, logging (never raising) release failures (§44)."""
        if self._inventory_repo is None:
            return
        for item, _variant, _product, _inv in lines:
            try:
                self._inventory_repo.release(tenant_id, item.variant_id, item.quantity)
            except Exception:
                logger.exception(
                    "Cart inventory compensation failed",
                    extra={
                        "action_id": action.action_id,
                        "tenant_id": tenant_id,
                        "variant_id": item.variant_id,
                    },
                )

    def _rollback_persisted_cart_orders(
        self,
        tenant_id: str,
        orders: list[Order],
        reason: str = "Saga compensation: cart checkout persistence failure",
        action: ActionRequest | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Roll back already-persisted orders to CANCELLED state (§41, §44).

        Secondary exceptions during compensation rollback are logged and suppressed
        to avoid masking the primary checkout/persistence failure.
        """
        if self._order_repo is None or not orders:
            return
        for order in orders:
            try:
                cancelled = order.cancel(reason=reason)
                self._order_repo.save(tenant_id, cancelled)
                if self._audit_ledger is not None:
                    if action is not None:
                        self._audit(
                            AuditEventType.ORDER_CANCELLED,
                            action,
                            tenant_id,
                            actor="order_service",
                            data={
                                "order_id": cancelled.order_id,
                                "reason": reason,
                            },
                            trace_id=trace_id,
                        )
                    else:
                        self._audit_ledger.record_event(
                            event_type=AuditEventType.ORDER_CANCELLED,
                            action_id=None,
                            tenant_id=tenant_id,
                            actor="order_service",
                            data={
                                "order_id": cancelled.order_id,
                                "reason": reason,
                            },
                            agent_id=order.agent_id,
                            trace_id=trace_id,
                        )
            except Exception:
                logger.exception(
                    "Cart order rollback compensation failed",
                    extra={
                        "tenant_id": tenant_id,
                        "order_id": order.order_id,
                    },
                )

    def _execute_cart_order_and_payment_pipeline(
        self,
        action: ActionRequest,
        tenant_id: str,
        cart: Cart,
        lines: list[tuple[CartItem, ProductVariant, Product, Inventory | None]],
        merchant_id: str,
        decision_summary: Decision | None = None,
        approval: Approval | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CartCheckoutResult:
        """Reserve-all → pay-once → confirm-all saga with full compensation (ADR-0006)."""
        if action.amount is None:
            return CartCheckoutResult(
                action_id=action.action_id,
                tenant_id=tenant_id,
                status=ActionStatus.FAILED,
                decision=PolicyDecision.DENY,
                cart=cart,
                rejection_reason="Amount missing",
            )

        # 1. Reserve every line, compensating already-reserved lines on failure (§44)
        reserved_count = 0
        if self._inventory_repo is not None:
            for item, _variant, _product, _inv in lines:
                try:
                    self._inventory_repo.reserve(tenant_id, item.variant_id, item.quantity)
                    reserved_count += 1
                except InsufficientInventoryError as err:
                    self._release_cart_reservations(tenant_id, lines[:reserved_count], action)
                    self._audit(
                        AuditEventType.ORDER_CANCELLED,
                        action,
                        tenant_id,
                        actor="order_service",
                        data={"cart_id": cart.cart_id, "reason": str(err)},
                        trace_id=trace_id,
                    )
                    return CartCheckoutResult(
                        action_id=action.action_id,
                        tenant_id=tenant_id,
                        status=ActionStatus.FAILED,
                        decision=PolicyDecision.DENY,
                        cart=cart,
                        rejection_reason=str(err),
                    )
                except Exception as exc:
                    self._release_cart_reservations(tenant_id, lines[:reserved_count], action)
                    self._audit(
                        AuditEventType.ORDER_CANCELLED,
                        action,
                        tenant_id,
                        actor="order_service",
                        data={
                            "cart_id": cart.cart_id,
                            "reason": f"Inventory reservation failed: {exc}",
                        },
                        trace_id=trace_id,
                    )
                    raise

        # 2. One payment for the whole cart (§39, §41)
        payment_attempt = None
        if self._payment_provider is not None:
            idem_key = idempotency_key or f"idem_{action.action_id}"
            try:
                payment_attempt = self._payment_provider.create_payment(
                    tenant_id=tenant_id,
                    intent_id=f"intent_{action.action_id}",
                    amount=action.amount,
                    idempotency_key=idem_key,
                )
            except Exception as exc:
                self._release_cart_reservations(tenant_id, lines, action)
                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={"cart_id": cart.cart_id, "reason": f"Payment creation failed: {exc}"},
                    trace_id=trace_id,
                )
                raise

            if self._payment_repo is not None:
                self._payment_repo.save(tenant_id, payment_attempt)

            prov_payment_id = payment_attempt.provider_payment_id or payment_attempt.attempt_id
            mapping = ProviderTransactionMapping(
                mapping_id=f"map_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                attempt_id=payment_attempt.attempt_id,
                provider=payment_attempt.provider,
                provider_payment_id=prov_payment_id,
            )
            self._provider_mapping_repo.save(mapping)
            if (
                payment_attempt.provider_payment_id
                and payment_attempt.provider_payment_id != payment_attempt.attempt_id
            ):
                alt_mapping = ProviderTransactionMapping(
                    mapping_id=f"map_{uuid.uuid4().hex[:16]}",
                    tenant_id=tenant_id,
                    attempt_id=payment_attempt.attempt_id,
                    provider=payment_attempt.provider,
                    provider_payment_id=payment_attempt.attempt_id,
                )
                self._provider_mapping_repo.save(alt_mapping)

            self._audit(
                AuditEventType.PAYMENT_CREATED,
                action,
                tenant_id,
                actor="payment_provider",
                data={
                    "payment_id": payment_attempt.attempt_id,
                    "status": payment_attempt.status.value,
                    "amount": str(action.amount),
                    "cart_id": cart.cart_id,
                },
                trace_id=trace_id,
            )

            if payment_attempt.status in (PaymentStatus.DECLINED, PaymentStatus.FAILED):
                self._release_cart_reservations(tenant_id, lines, action)
                self._audit(
                    AuditEventType.PAYMENT_FAILED,
                    action,
                    tenant_id,
                    actor="payment_provider",
                    data={
                        "payment_id": payment_attempt.attempt_id,
                        "status": payment_attempt.status.value,
                        "cart_id": cart.cart_id,
                    },
                    trace_id=trace_id,
                )
                if self._idempotency_store is not None and idempotency_key is not None:
                    self._idempotency_store.save(
                        tenant_id,
                        IdempotencyRecord(
                            key=idempotency_key,
                            tenant_id=tenant_id,
                            operation_type="cart_checkout",
                            request_hash=action.action_id,
                            result_data={
                                "cart_id": cart.cart_id,
                                "payment_id": payment_attempt.attempt_id,
                                "status": "FAILED",
                                "decision": PolicyDecision.ALLOW.value,
                                "amount_minor": action.amount.minor_units,
                                "currency": action.amount.currency,
                                "action_id": action.action_id,
                            },
                        ),
                    )
                return CartCheckoutResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.ALLOW,
                    cart=cart,
                    payment=payment_attempt,
                    rejection_reason="Payment was declined by provider",
                )

            if payment_attempt.status is PaymentStatus.UNKNOWN:
                self._audit(
                    AuditEventType.PAYMENT_UNKNOWN,
                    action,
                    tenant_id,
                    actor="payment_provider",
                    data={
                        "payment_id": payment_attempt.attempt_id,
                        "status": "UNKNOWN",
                        "cart_id": cart.cart_id,
                    },
                    trace_id=trace_id,
                )
                if self._idempotency_store is not None and idempotency_key is not None:
                    self._idempotency_store.save(
                        tenant_id,
                        IdempotencyRecord(
                            key=idempotency_key,
                            tenant_id=tenant_id,
                            operation_type="cart_checkout",
                            request_hash=action.action_id,
                            result_data={
                                "cart_id": cart.cart_id,
                                "payment_id": payment_attempt.attempt_id,
                                "status": "FAILED",
                                "decision": PolicyDecision.ALLOW.value,
                                "amount_minor": action.amount.minor_units,
                                "currency": action.amount.currency,
                                "action_id": action.action_id,
                            },
                        ),
                    )
                return CartCheckoutResult(
                    action_id=action.action_id,
                    tenant_id=tenant_id,
                    status=ActionStatus.FAILED,
                    decision=PolicyDecision.ALLOW,
                    cart=cart,
                    payment=payment_attempt,
                    rejection_reason="Payment gateway timeout; pending reconciliation",
                )

        # 3. One CONFIRMED single-variant order per line, sharing the payment (§41, ADR-0006)
        orders: list[Order] = []
        for item, variant, _product, _inv in lines:
            order = Order(
                order_id=f"ord_{uuid.uuid4().hex[:16]}",
                tenant_id=tenant_id,
                merchant_id=merchant_id,
                buyer_id=action.principal_id,
                agent_id=action.agent_id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                total_amount=variant.price.multiply(item.quantity),
                status=OrderStatus.CONFIRMED,
                payment_attempt_id=payment_attempt.attempt_id if payment_attempt else None,
            )
            if self._order_repo is not None:
                try:
                    self._order_repo.save(tenant_id, order)
                except Exception as exc:
                    if (
                        self._payment_provider is not None
                        and payment_attempt is not None
                        and payment_attempt.status is PaymentStatus.CAPTURED
                    ):
                        try:
                            self._payment_provider.refund(
                                tenant_id=tenant_id,
                                attempt_id=payment_attempt.attempt_id,
                                amount=action.amount,
                                reason="Cart order persistence failure compensation",
                            )
                        except Exception:
                            logger.exception(
                                "Cart payment refund compensation failed",
                                extra={
                                    "action_id": action.action_id,
                                    "tenant_id": tenant_id,
                                    "attempt_id": payment_attempt.attempt_id,
                                },
                            )
                    self._rollback_persisted_cart_orders(
                        tenant_id,
                        orders,
                        reason="Saga compensation: cart checkout persistence failure",
                        action=action,
                        trace_id=trace_id,
                    )
                    self._release_cart_reservations(tenant_id, lines, action)
                    self._audit(
                        AuditEventType.ORDER_CANCELLED,
                        action,
                        tenant_id,
                        actor="order_service",
                        data={
                            "cart_id": cart.cart_id,
                            "order_id": order.order_id,
                            "reason": f"Order persistence failed after payment capture: {exc}",
                        },
                        trace_id=trace_id,
                    )
                    raise
            orders.append(order)

        checked_out_cart = cart
        if self._cart_repo is not None:
            try:
                checked_out_cart = cart.mark_checked_out()
                self._cart_repo.save(tenant_id, checked_out_cart)
            except Exception as exc:
                if (
                    self._payment_provider is not None
                    and payment_attempt is not None
                    and payment_attempt.status is PaymentStatus.CAPTURED
                ):
                    try:
                        self._payment_provider.refund(
                            tenant_id=tenant_id,
                            attempt_id=payment_attempt.attempt_id,
                            amount=action.amount,
                            reason="Cart checkout persistence failure compensation",
                        )
                    except Exception:
                        logger.exception(
                            "Cart payment refund compensation failed after cart save failure",
                            extra={
                                "action_id": action.action_id,
                                "tenant_id": tenant_id,
                                "attempt_id": payment_attempt.attempt_id,
                            },
                        )
                self._rollback_persisted_cart_orders(
                    tenant_id,
                    orders,
                    reason="Saga compensation: cart checkout persistence failure",
                    action=action,
                    trace_id=trace_id,
                )
                self._release_cart_reservations(tenant_id, lines, action)
                self._audit(
                    AuditEventType.ORDER_CANCELLED,
                    action,
                    tenant_id,
                    actor="order_service",
                    data={"cart_id": cart.cart_id, "reason": f"Cart save failed: {exc}"},
                    trace_id=trace_id,
                )
                raise

        for order in orders:
            self._audit(
                AuditEventType.ORDER_CREATED,
                action,
                tenant_id,
                actor="order_service",
                data={
                    "order_id": order.order_id,
                    "status": order.status.value,
                    "cart_id": cart.cart_id,
                },
                trace_id=trace_id,
            )

        if self._idempotency_store is not None and idempotency_key is not None:
            self._idempotency_store.save(
                tenant_id,
                IdempotencyRecord(
                    key=idempotency_key,
                    tenant_id=tenant_id,
                    operation_type="cart_checkout",
                    request_hash=action.action_id,
                    result_data={
                        "cart_id": cart.cart_id,
                        "order_ids": [order.order_id for order in orders],
                        "payment_id": payment_attempt.attempt_id if payment_attempt else None,
                        "status": "COMPLETED",
                        "decision": PolicyDecision.ALLOW.value,
                        "amount_minor": action.amount.minor_units,
                        "currency": action.amount.currency,
                        "action_id": action.action_id,
                    },
                ),
            )

        self._record_daily_spend(tenant_id, action.agent_id, action.amount.minor_units)

        return CartCheckoutResult(
            action_id=action.action_id,
            tenant_id=tenant_id,
            status=ActionStatus.COMPLETED,
            decision=PolicyDecision.ALLOW,
            cart=checked_out_cart,
            orders=tuple(orders),
            payment=payment_attempt,
            approval=approval,
            decision_summary=decision_summary,
        )

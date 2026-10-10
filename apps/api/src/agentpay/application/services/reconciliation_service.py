"""Payment timeout reconciliation service (§43).

On timeout the platform must query the provider for the authoritative status instead of
blindly retrying a charge. This service encapsulates exactly that: it performs a read-only
provider query for attempts stuck in UNKNOWN and persists the outcome. It never creates,
captures, or refunds — reconciliation observes, it does not move money.
"""

from __future__ import annotations

from agentpay.application.ports.payment_port import PaymentProvider, PaymentRepository
from agentpay.domain.errors import CrossTenantAccessError, InvalidPaymentError
from agentpay.domain.payments import PaymentAttempt, PaymentStatus


class PaymentReconciler:
    """Resolve UNKNOWN payment attempts by querying the provider (§43)."""

    def __init__(self, payment_provider: PaymentProvider, payment_repo: PaymentRepository) -> None:
        self._provider = payment_provider
        self._repo = payment_repo

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

"""Unit tests for timeout reconciliation (§43).

Reconciliation queries the provider for the authoritative status of UNKNOWN attempts.
It must never create, capture, or refund — observing must not move money.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentpay.adapters.in_memory.payment_repository import InMemoryPaymentRepository
from agentpay.application.ports.payment_port import PaymentProvider, PaymentRepository
from agentpay.application.services.reconciliation_service import PaymentReconciler
from agentpay.domain.errors import CrossTenantAccessError, InvalidPaymentError
from agentpay.domain.money import Money
from agentpay.domain.payments import PaymentAttempt, PaymentStatus, Refund


def make_attempt(
    attempt_id: str = "pay_01",
    tenant_id: str = "tenant_01",
    status: PaymentStatus = PaymentStatus.UNKNOWN,
) -> PaymentAttempt:
    return PaymentAttempt(
        attempt_id=attempt_id,
        intent_id="intent_01",
        tenant_id=tenant_id,
        amount=Money(129900, "INR"),
        status=status,
        idempotency_key="idem_01",
        provider="mock_razorpay",
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


class CountingProvider(PaymentProvider):
    """Test double that records exactly which provider operations reconciliation triggers."""

    def __init__(self, authoritative: PaymentAttempt) -> None:
        self.authoritative = authoritative
        self.get_calls: list[tuple[str, str]] = []
        self.create_calls: list[tuple[str, str]] = []
        self.capture_calls: list[tuple[str, str]] = []
        self.refund_calls: list[tuple[str, str]] = []

    def create_payment(
        self,
        tenant_id: str,
        intent_id: str,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentAttempt:
        self.create_calls.append((tenant_id, intent_id))
        return self.authoritative

    def get_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        self.get_calls.append((tenant_id, attempt_id))
        return self.authoritative

    def capture_payment(self, tenant_id: str, attempt_id: str) -> PaymentAttempt:
        self.capture_calls.append((tenant_id, attempt_id))
        return self.authoritative

    def refund(
        self,
        tenant_id: str,
        attempt_id: str,
        amount: Money,
        reason: str,
    ) -> Refund:
        self.refund_calls.append((tenant_id, attempt_id))
        raise AssertionError("reconciliation must never refund")


def build(
    stored: PaymentAttempt | None = None,
    authoritative: PaymentAttempt | None = None,
) -> tuple[PaymentReconciler, CountingProvider, InMemoryPaymentRepository]:
    stored_attempt = stored or make_attempt()
    provider = CountingProvider(authoritative or stored_attempt)
    repo = InMemoryPaymentRepository()
    repo.save(stored_attempt.tenant_id, stored_attempt)
    return PaymentReconciler(provider, repo), provider, repo


def test_unknown_attempt_is_resolved_by_single_provider_query() -> None:
    reconciler, provider, _ = build(authoritative=make_attempt(status=PaymentStatus.CAPTURED))
    result = reconciler.reconcile_unknown_payment("tenant_01", "pay_01")
    assert result.status is PaymentStatus.CAPTURED
    assert provider.get_calls == [("tenant_01", "pay_01")]


def test_reconciliation_performs_no_financial_operation() -> None:
    reconciler, provider, _ = build(authoritative=make_attempt(status=PaymentStatus.CAPTURED))
    reconciler.reconcile_unknown_payment("tenant_01", "pay_01")
    assert provider.create_calls == []
    assert provider.capture_calls == []
    assert provider.refund_calls == []


def test_reconciled_status_is_persisted() -> None:
    reconciler, _, repo = build(authoritative=make_attempt(status=PaymentStatus.FAILED))
    reconciler.reconcile_unknown_payment("tenant_01", "pay_01")
    persisted = repo.get("tenant_01", "pay_01")
    assert persisted is not None
    assert persisted.status is PaymentStatus.FAILED


def test_settled_attempt_never_contacts_the_provider() -> None:
    reconciler, provider, _ = build(stored=make_attempt(status=PaymentStatus.CAPTURED))
    result = reconciler.reconcile_unknown_payment("tenant_01", "pay_01")
    assert result.status is PaymentStatus.CAPTURED
    assert provider.get_calls == []


def test_unknown_provider_answer_is_persisted_without_retry() -> None:
    reconciler, provider, _ = build(authoritative=make_attempt(status=PaymentStatus.UNKNOWN))
    result = reconciler.reconcile_unknown_payment("tenant_01", "pay_01")
    assert result.status is PaymentStatus.UNKNOWN
    assert len(provider.get_calls) == 1
    assert provider.create_calls == []


def test_missing_attempt_raises_without_provider_contact() -> None:
    reconciler, provider, _ = build()
    with pytest.raises(InvalidPaymentError):
        reconciler.reconcile_unknown_payment("tenant_01", "pay_missing")
    assert provider.get_calls == []


def test_cross_tenant_attempt_is_rejected() -> None:
    """A repository that leaks a foreign-tenant record must still fail closed here."""

    class LeakingRepository(PaymentRepository):
        def get(self, tenant_id: str, attempt_id: str) -> PaymentAttempt | None:
            return make_attempt()

        def save(self, tenant_id: str, attempt: PaymentAttempt) -> None:
            raise AssertionError("must fail before any write")

    provider = CountingProvider(make_attempt())
    reconciler = PaymentReconciler(provider, LeakingRepository())
    with pytest.raises(CrossTenantAccessError):
        reconciler.reconcile_unknown_payment("tenant_02", "pay_01")
    assert provider.get_calls == []


def test_mismatched_provider_answer_is_rejected() -> None:
    reconciler, _, _ = build(authoritative=make_attempt(attempt_id="pay_other"))
    with pytest.raises(InvalidPaymentError):
        reconciler.reconcile_unknown_payment("tenant_01", "pay_01")


def test_reconcile_timeout_delegates_to_reconcile_unknown_payment() -> None:
    """reconcile_timeout(attempt) must extract tenant and attempt id and delegate to reconcile_unknown_payment (§43, §104)."""
    attempt = make_attempt(status=PaymentStatus.UNKNOWN)
    reconciler, provider, repo = build(
        stored=attempt, authoritative=make_attempt(status=PaymentStatus.CAPTURED)
    )
    result = reconciler.reconcile_timeout(attempt)
    assert result.status is PaymentStatus.CAPTURED
    assert len(provider.get_calls) == 1
    assert provider.get_calls[0] == ("tenant_01", "pay_01")
    persisted = repo.get("tenant_01", "pay_01")
    assert persisted is not None
    assert persisted.status is PaymentStatus.CAPTURED

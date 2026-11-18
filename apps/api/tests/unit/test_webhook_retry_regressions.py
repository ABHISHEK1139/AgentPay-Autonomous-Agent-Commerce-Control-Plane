"""Webhook event retry and replay-state regression tests."""

from agentpay.adapters.in_memory.payment_repository import InMemoryProviderWebhookEventRepository
from agentpay.domain.payments import WebhookEventStatus


def test_failed_webhook_event_can_be_reclaimed_for_redelivery() -> None:
    repository = InMemoryProviderWebhookEventRepository()
    first, is_new = repository.claim_event(
        provider="stripe", event_id="evt_retry", payload_hash="hash-1"
    )
    assert is_new is True
    assert first.status is WebhookEventStatus.PROCESSING
    failed = first.mark_failed("TEMPORARY_FAILURE")
    repository.save(failed)

    retried, is_retry = repository.claim_event(
        provider="stripe", event_id="evt_retry", payload_hash="hash-1"
    )
    assert is_retry is True
    assert retried.status is WebhookEventStatus.PROCESSING
    assert retried.attempt_count == 2


def test_processed_webhook_event_remains_replay_blocked() -> None:
    repository = InMemoryProviderWebhookEventRepository()
    first, _ = repository.claim_event(provider="stripe", event_id="evt_done")
    repository.save(first.mark_processed("payment-1"))

    duplicate, is_new = repository.claim_event(provider="stripe", event_id="evt_done")
    assert is_new is False
    assert duplicate.status is WebhookEventStatus.PROCESSED

"""Explicit zero webhook amounts must never be treated as omitted fields."""

import pytest
from test_webhooks import _seed_test_env

from agentpay.domain.payments import PaymentStatus


@pytest.mark.parametrize("event_type", ["payment.captured", "payment.refunded"])
def test_zero_webhook_amount_is_rejected_without_payment_mutation(event_type: str) -> None:
    _, client, env = _seed_test_env()
    attempt = env["payment_repo"].get("tenant_01", "att_test_01")
    assert attempt is not None
    env["payment_repo"].save(
        "tenant_01", attempt.transition_to(PaymentStatus.CAPTURED, provider_payment_id="prov_zero")
    )
    response = client.post_webhook(
        event_type,
        {
            "event_id": f"evt_zero_{event_type}",
            "event_type": event_type,
            "attempt_id": "att_test_01",
            "refund_id": "ref_zero",
            "amount_minor": 0,
            "currency": "INR",
        },
    )
    assert response.status_code == 400
    stored = env["payment_repo"].get("tenant_01", "att_test_01")
    assert stored is not None
    assert stored.status is PaymentStatus.CAPTURED

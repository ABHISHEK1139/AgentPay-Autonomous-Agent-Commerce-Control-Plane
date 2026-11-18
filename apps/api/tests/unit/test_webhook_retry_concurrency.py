"""Concurrency regression for durable webhook retry claims."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentpay.domain.payments import ProviderWebhookEvent, WebhookEventStatus
from agentpay.infrastructure.database.models import Base
from agentpay.infrastructure.database.repositories.webhook_event_repository import (
    SqlAlchemyProviderWebhookEventRepository,
)


def test_only_one_sql_retry_claim_wins(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'webhooks.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed = SqlAlchemyProviderWebhookEventRepository(factory)
    event = ProviderWebhookEvent(
        id="pwe_retry_race",
        provider="stripe",
        event_id="evt_retry_race",
        status=WebhookEventStatus.FAILED,
        received_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        attempt_count=1,
        last_error_code="TEMPORARY_FAILURE",
    )
    seed.save(event)

    def claim() -> bool:
        repository = SqlAlchemyProviderWebhookEventRepository(factory)
        _, won = repository.claim_event(provider="stripe", event_id="evt_retry_race")
        return won

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert results.count(True) == 1
    final = seed.find_by_provider_event("stripe", "evt_retry_race")
    assert final is not None
    assert final.status is WebhookEventStatus.PROCESSING
    assert final.attempt_count == 2
    engine.dispose()

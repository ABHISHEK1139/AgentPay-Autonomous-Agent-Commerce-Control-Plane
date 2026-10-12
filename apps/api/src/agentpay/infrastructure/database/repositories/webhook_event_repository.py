"""SQLAlchemy implementation of ProviderWebhookEventRepository port (§42, §49, §61, AP-003)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from agentpay.application.ports.payment_port import ProviderWebhookEventRepository
from agentpay.domain.payments import ProviderWebhookEvent, WebhookEventStatus
from agentpay.infrastructure.database.models.provider_webhook_event import (
    ProviderWebhookEventModel,
)
from agentpay.infrastructure.database.repositories.base import BaseSqlAlchemyRepository


class SqlAlchemyProviderWebhookEventRepository(
    BaseSqlAlchemyRepository, ProviderWebhookEventRepository
):
    """SQLAlchemy-backed repository for ProviderWebhookEvent persistence and atomic deduplication."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        super().__init__(session_or_factory)

    def get(self, event_pk: str) -> ProviderWebhookEvent | None:
        with self._session() as session:
            model = session.get(ProviderWebhookEventModel, event_pk)
            return model.to_domain() if model is not None else None

    def find_by_provider_event(
        self,
        provider: str,
        event_id: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        acc_id = provider_account_id or ""
        with self._session() as session:
            stmt = select(ProviderWebhookEventModel).where(
                ProviderWebhookEventModel.provider == provider,
                ProviderWebhookEventModel.event_id == event_id,
            )
            if acc_id:
                stmt = stmt.where(ProviderWebhookEventModel.provider_account_id == acc_id)
            model = session.scalar(stmt)
            if model is None and not acc_id:
                model = session.scalar(
                    select(ProviderWebhookEventModel).where(
                        ProviderWebhookEventModel.provider == provider,
                        ProviderWebhookEventModel.event_id == event_id,
                    )
                )
            return model.to_domain() if model is not None else None

    def save(self, event: ProviderWebhookEvent) -> None:
        with self._session() as session:
            existing = session.get(ProviderWebhookEventModel, event.id)
            if existing is None:
                acc_id = event.provider_account_id or ""
                stmt = select(ProviderWebhookEventModel).where(
                    ProviderWebhookEventModel.provider == event.provider,
                    ProviderWebhookEventModel.provider_account_id == acc_id,
                    ProviderWebhookEventModel.event_id == event.event_id,
                )
                existing = session.scalar(stmt)
            if existing is None:
                session.add(ProviderWebhookEventModel.from_domain(event))
            else:
                existing.status = event.status.value
                existing.attempt_count = event.attempt_count
                existing.last_error_code = event.last_error_code
                existing.entity_reference = event.entity_reference
                existing.updated_at = event.updated_at
                existing.provider_account_id = event.provider_account_id or ""
                existing.event_type = event.event_type
                existing.payload_hash = event.payload_hash
                existing.trace_id = event.trace_id
            session.commit()

    def claim_event(
        self,
        provider: str | ProviderWebhookEvent,
        provider_account_id: str = "",
        event_id: str = "",
        payload_hash: str | None = None,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[ProviderWebhookEvent, bool]:
        """Atomically claim a webhook event using database uniqueness constraint.

        Survives multi-process concurrency and restarts.
        """
        if isinstance(provider, ProviderWebhookEvent):
            event = provider
            prov = event.provider
            acc_id = event.provider_account_id or ""
            evt_id = event.event_id
        else:
            prov = provider
            acc_id = provider_account_id or ""
            evt_id = event_id
            event = ProviderWebhookEvent(
                id=f"pwe_{uuid.uuid4().hex[:12]}",
                provider=prov,
                provider_account_id=acc_id,
                event_id=evt_id,
                status=WebhookEventStatus.PROCESSING,
                payload_hash=payload_hash,
                event_type=event_type,
                trace_id=trace_id,
            )

        with self._session() as session:
            try:
                model = ProviderWebhookEventModel.from_domain(event)
                session.add(model)
                session.commit()
                return event, True
            except IntegrityError:
                session.rollback()
                stmt = select(ProviderWebhookEventModel).where(
                    ProviderWebhookEventModel.provider == prov,
                    ProviderWebhookEventModel.event_id == evt_id,
                )
                if acc_id:
                    stmt = stmt.where(ProviderWebhookEventModel.provider_account_id == acc_id)
                existing = session.scalar(stmt)
                if existing is not None:
                    return existing.to_domain(), False

                existing_any = session.scalar(
                    select(ProviderWebhookEventModel).where(
                        ProviderWebhookEventModel.provider == prov,
                        ProviderWebhookEventModel.event_id == evt_id,
                    )
                )
                if existing_any is not None:
                    return existing_any.to_domain(), False

                existing_pk = session.get(ProviderWebhookEventModel, event.id)
                if existing_pk is not None:
                    return existing_pk.to_domain(), False
                raise

    def mark_processed(
        self,
        provider: str,
        event_id: str,
        entity_reference: str | None = None,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        acc_id = provider_account_id or ""
        with self._session() as session:
            stmt = select(ProviderWebhookEventModel).where(
                ProviderWebhookEventModel.provider == provider,
                ProviderWebhookEventModel.event_id == event_id,
            )
            if acc_id:
                stmt = stmt.where(ProviderWebhookEventModel.provider_account_id == acc_id)
            model = session.scalar(stmt)
            if model is None and not acc_id:
                model = session.scalar(
                    select(ProviderWebhookEventModel).where(
                        ProviderWebhookEventModel.provider == provider,
                        ProviderWebhookEventModel.event_id == event_id,
                    )
                )
            if model is None:
                return None
            domain_ev = model.to_domain().mark_processed(entity_reference)
            model.status = domain_ev.status.value
            model.entity_reference = domain_ev.entity_reference
            model.last_error_code = None
            model.updated_at = domain_ev.updated_at
            session.commit()
            return domain_ev

    def mark_failed(
        self,
        provider: str,
        event_id: str,
        error_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        acc_id = provider_account_id or ""
        with self._session() as session:
            stmt = select(ProviderWebhookEventModel).where(
                ProviderWebhookEventModel.provider == provider,
                ProviderWebhookEventModel.event_id == event_id,
            )
            if acc_id:
                stmt = stmt.where(ProviderWebhookEventModel.provider_account_id == acc_id)
            model = session.scalar(stmt)
            if model is None and not acc_id:
                model = session.scalar(
                    select(ProviderWebhookEventModel).where(
                        ProviderWebhookEventModel.provider == provider,
                        ProviderWebhookEventModel.event_id == event_id,
                    )
                )
            if model is None:
                return None
            domain_ev = model.to_domain().mark_failed(error_code)
            model.status = domain_ev.status.value
            model.last_error_code = domain_ev.last_error_code
            model.updated_at = domain_ev.updated_at
            session.commit()
            return domain_ev

    def mark_quarantined(
        self,
        provider: str,
        event_id: str,
        reason_code: str,
        provider_account_id: str = "",
    ) -> ProviderWebhookEvent | None:
        acc_id = provider_account_id or ""
        with self._session() as session:
            stmt = select(ProviderWebhookEventModel).where(
                ProviderWebhookEventModel.provider == provider,
                ProviderWebhookEventModel.event_id == event_id,
            )
            if acc_id:
                stmt = stmt.where(ProviderWebhookEventModel.provider_account_id == acc_id)
            model = session.scalar(stmt)
            if model is None and not acc_id:
                model = session.scalar(
                    select(ProviderWebhookEventModel).where(
                        ProviderWebhookEventModel.provider == provider,
                        ProviderWebhookEventModel.event_id == event_id,
                    )
                )
            if model is None:
                return None
            domain_ev = model.to_domain().mark_quarantined(reason_code)
            model.status = domain_ev.status.value
            model.last_error_code = domain_ev.last_error_code
            model.updated_at = domain_ev.updated_at
            session.commit()
            return domain_ev

"""SQLAlchemy implementation of EmergencyPauseStore port (§21, §67, AP-008)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agentpay.application.ports.emergency_pause_port import EmergencyPauseStore, PauseRecord
from agentpay.infrastructure.database.models.emergency_pause import EmergencyPauseRecordModel

logger = logging.getLogger(__name__)


class SqlAlchemyEmergencyPauseRepository(EmergencyPauseStore):
    """Database-backed emergency pause repository ensuring cluster-wide coordination."""

    def __init__(self, session_factory: sessionmaker[Any]) -> None:
        self._session_factory = session_factory

    def _normalize_scope(self, scope: str) -> str:
        s = scope.strip() if scope else "ALL"
        upper = s.upper()
        if upper in ("ALL", "GLOBAL", "CLUSTER"):
            return "ALL"
        if s.startswith("tenant:"):
            return s
        return f"tenant:{s}"

    def is_globally_paused(self) -> bool:
        """Return True if global 'ALL' pause is active. Fails closed on database outage."""
        try:
            with self._session_factory() as session:
                stmt = select(EmergencyPauseRecordModel).where(
                    EmergencyPauseRecordModel.scope == "ALL",
                    EmergencyPauseRecordModel.is_paused.is_(True),
                )
                row = session.execute(stmt).scalar_one_or_none()
                return row is not None
        except Exception as exc:
            logger.error("Failed to query global emergency pause state; failing closed: %s", exc)
            return True  # Fail closed (§2.4, AP-008)

    def is_paused_for(self, tenant_id: str = "") -> bool:
        """Return True if tenant or global pause is active. Fails closed on database outage."""
        try:
            with self._session_factory() as session:
                stmt_global = select(EmergencyPauseRecordModel).where(
                    EmergencyPauseRecordModel.scope == "ALL",
                    EmergencyPauseRecordModel.is_paused.is_(True),
                )
                if session.execute(stmt_global).scalar_one_or_none() is not None:
                    return True

                if not tenant_id:
                    return False

                clean = tenant_id.strip()
                scopes = [f"tenant:{clean}", clean]
                stmt_tenant = select(EmergencyPauseRecordModel).where(
                    EmergencyPauseRecordModel.scope.in_(scopes),
                    EmergencyPauseRecordModel.is_paused.is_(True),
                )
                return session.execute(stmt_tenant).scalar_one_or_none() is not None
        except Exception as exc:
            logger.error(
                "Failed to query emergency pause state for tenant %s; failing closed: %s",
                tenant_id,
                exc,
            )
            return True  # Fail closed (§2.4, AP-008)

    def pause(self, operator_id: str, reason: str, scope: str = "ALL") -> PauseRecord:
        norm = self._normalize_scope(scope)
        now = datetime.now(UTC)
        record = PauseRecord(
            scope=norm,
            is_paused=True,
            paused_by=operator_id,
            reason=reason,
            paused_at=now,
        )
        with self._session_factory() as session:
            existing = session.get(EmergencyPauseRecordModel, norm)
            if existing is not None:
                existing.is_paused = True
                existing.paused_by = operator_id
                existing.reason = reason
                existing.paused_at = now
                existing.resumed_by = None
                existing.resumed_at = None
            else:
                session.add(EmergencyPauseRecordModel.from_domain(record))
            session.commit()
        return record

    def resume(self, operator_id: str, scope: str = "ALL") -> PauseRecord:
        norm = self._normalize_scope(scope)
        now = datetime.now(UTC)
        with self._session_factory() as session:
            if norm == "ALL":
                # Resuming ALL marks all pause records as resumed
                stmt = select(EmergencyPauseRecordModel).where(
                    EmergencyPauseRecordModel.is_paused.is_(True)
                )
                rows = session.execute(stmt).scalars().all()
                for row in rows:
                    row.is_paused = False
                    row.resumed_by = operator_id
                    row.resumed_at = now
                record = PauseRecord(
                    scope="ALL",
                    is_paused=False,
                    paused_by=operator_id,
                    reason="Resumed cluster",
                    paused_at=now,
                    resumed_by=operator_id,
                    resumed_at=now,
                )
            else:
                existing = session.get(EmergencyPauseRecordModel, norm)
                if existing is not None:
                    existing.is_paused = False
                    existing.resumed_by = operator_id
                    existing.resumed_at = now
                    record = existing.to_domain()
                else:
                    record = PauseRecord(
                        scope=norm,
                        is_paused=False,
                        paused_by=operator_id,
                        reason="Resumed",
                        paused_at=now,
                        resumed_by=operator_id,
                        resumed_at=now,
                    )
            session.commit()
        return record

    def get_status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            stmt = select(EmergencyPauseRecordModel).where(
                EmergencyPauseRecordModel.is_paused.is_(True)
            )
            rows = session.execute(stmt).scalars().all()
            active_scopes = [r.scope for r in rows if r.is_paused]
            is_paused = "ALL" in active_scopes or len(active_scopes) > 0

            latest_rec = max(rows, key=lambda r: r.paused_at) if rows else None
            metadata: dict[str, Any] = {}
            if latest_rec:
                metadata = {
                    "paused_by": latest_rec.paused_by,
                    "reason": latest_rec.reason,
                    "scope": latest_rec.scope,
                    "paused_at": latest_rec.paused_at.isoformat(),
                    "active_scopes": sorted(active_scopes),
                }

            return {
                "paused": is_paused,
                "emergency_paused": is_paused,
                "scopes": sorted(active_scopes),
                "metadata": metadata,
            }

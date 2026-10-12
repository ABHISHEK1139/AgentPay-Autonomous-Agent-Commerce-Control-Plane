"""In-Memory Thread-Safe Emergency Pause Store (§21, §67, AP-008).

Provides lock-guarded, in-memory implementation of the EmergencyPauseStore port.
Used for local testing, development, and can be shared between multi-threaded app instances.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from agentpay.application.ports.emergency_pause_port import EmergencyPauseStore, PauseRecord


class InMemoryEmergencyPauseStore(EmergencyPauseStore):
    """Thread-safe in-memory emergency pause store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, PauseRecord] = {}

    def _normalize_scope(self, scope: str) -> str:
        s = scope.strip() if scope else "ALL"
        upper = s.upper()
        if upper in ("ALL", "GLOBAL", "CLUSTER"):
            return "ALL"
        if s.startswith("tenant:"):
            return s
        return f"tenant:{s}"

    def is_globally_paused(self) -> bool:
        with self._lock:
            rec = self._records.get("ALL")
            return bool(rec and rec.is_paused)

    def is_paused_for(self, tenant_id: str = "") -> bool:
        with self._lock:
            global_rec = self._records.get("ALL")
            if global_rec and global_rec.is_paused:
                return True
            if not tenant_id:
                return False
            clean = tenant_id.strip()
            direct_rec = self._records.get(f"tenant:{clean}") or self._records.get(clean)
            return bool(direct_rec and direct_rec.is_paused)

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
        with self._lock:
            self._records[norm] = record
            if norm == "ALL":
                # Ensure alias is present
                self._records["ALL"] = record
            else:
                raw_tenant = norm[len("tenant:") :]
                self._records[raw_tenant] = record
        return record

    def resume(self, operator_id: str, scope: str = "ALL") -> PauseRecord:
        norm = self._normalize_scope(scope)
        now = datetime.now(UTC)
        with self._lock:
            existing = self._records.get(norm)
            reason = existing.reason if existing else "Resumed"
            paused_at = existing.paused_at if existing else now

            record = PauseRecord(
                scope=norm,
                is_paused=False,
                paused_by=existing.paused_by if existing else operator_id,
                reason=reason,
                paused_at=paused_at,
                resumed_by=operator_id,
                resumed_at=now,
            )
            if norm == "ALL":
                # Resuming ALL clears all pause records
                self._records.clear()
            else:
                self._records.pop(norm, None)
                raw_tenant = norm[len("tenant:") :]
                self._records.pop(raw_tenant, None)
        return record

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            active_scopes = [
                k for k, v in self._records.items() if v.is_paused and not k.startswith("tenant:")
            ]
            global_rec = self._records.get("ALL")
            global_paused = bool(global_rec and global_rec.is_paused)
            is_paused = global_paused or len(active_scopes) > 0

            latest_rec = None
            if self._records:
                latest_rec = max(self._records.values(), key=lambda r: r.paused_at)

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

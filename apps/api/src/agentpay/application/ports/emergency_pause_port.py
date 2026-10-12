"""Emergency Kill Switch & Pause State Port (§21, §67, AP-008).

Defines the contract for persistent, cluster-wide emergency pause state:
- Supports global ('ALL') and tenant-scoped ('tenant:<tenant_id>') pause records.
- Guarantees multi-replica coordination across distributed instances.
- Fails closed if pause state cannot be authoritative or consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PauseRecord:
    """Represents a persisted pause state for a given operational scope."""

    scope: str
    is_paused: bool
    paused_by: str
    reason: str
    paused_at: datetime
    resumed_by: str | None = None
    resumed_at: datetime | None = None


@runtime_checkable
class EmergencyPauseStore(Protocol):
    """Port for authoritative emergency kill-switch pause storage (§21, §67, AP-008)."""

    def is_paused_for(self, tenant_id: str = "") -> bool:
        """Return True if specified tenant or global scope is paused."""
        ...

    def is_globally_paused(self) -> bool:
        """Return True if cluster-wide global pause ('ALL') is active."""
        ...

    def pause(self, operator_id: str, reason: str, scope: str = "ALL") -> PauseRecord:
        """Set pause state for specified scope."""
        ...

    def resume(self, operator_id: str, scope: str = "ALL") -> PauseRecord:
        """Resume operational state for specified scope."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Return serialized summary of active pause states."""
        ...

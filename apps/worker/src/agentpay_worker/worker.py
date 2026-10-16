"""Background worker runtime (§104, §105, §106)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from agentpay.application.services.event_bus import EventBus
from agentpay.domain.approval import ApprovalStatus
from agentpay.domain.payments import PaymentStatus
from agentpay_worker.dispatcher import OutboxDispatcher

logger = logging.getLogger(__name__)


class BackgroundWorker:
    """Unified background worker running outbox dispatching and reconciliation jobs (§104, §105, §106)."""

    def __init__(
        self,
        outbox_repo: Any | None = None,
        event_bus: EventBus | None = None,
        payment_repo: Any | None = None,
        payment_reconciler: Any | None = None,
        approval_repo: Any | None = None,
        approval_ttl_seconds: int = 3600,
        payment_timeout_seconds: int = 300,
        worker_id: str | None = None,
        outbox_lease_seconds: int = 30,
    ) -> None:
        self.outbox_repo = outbox_repo
        self.event_bus = event_bus or EventBus()
        self.payment_repo = payment_repo
        self.payment_reconciler = payment_reconciler
        self.approval_repo = approval_repo
        self.approval_ttl_seconds = approval_ttl_seconds
        self.payment_timeout_seconds = payment_timeout_seconds
        self.worker_id = worker_id or f"wrk_{uuid.uuid4().hex[:12]}"
        self.outbox_lease_seconds = outbox_lease_seconds

        self.dispatcher = (
            OutboxDispatcher(
                self.outbox_repo,
                self.event_bus,
                worker_id=self.worker_id,
                lease_seconds=self.outbox_lease_seconds,
            )
            if self.outbox_repo
            else None
        )

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def run_once(self) -> dict[str, int]:
        """Executes a single tick across all background jobs.

        Designed for hermetic, deterministic testing without background threads.
        """
        stats = {
            "outbox_dispatched": 0,
            "outbox_failed": 0,
            "reconciled_payments": 0,
            "expired_approvals": 0,
        }

        # 1. Outbox dispatch
        if self.dispatcher:
            dispatched, failed = self.dispatcher.dispatch_batch()
            stats["outbox_dispatched"] = dispatched
            stats["outbox_failed"] = failed

        # 2. Payment reconciliation for stuck UNKNOWN attempts
        if self.payment_repo and self.payment_reconciler:
            stats["reconciled_payments"] = self._reconcile_stuck_payments()

        # 3. Approval expiration sweep
        if self.approval_repo:
            stats["expired_approvals"] = self._expire_stale_approvals()

        return stats

    def _reconcile_stuck_payments(self) -> int:
        if self.payment_repo is None or self.payment_reconciler is None:
            return 0

        reconciled = 0
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.payment_timeout_seconds)

        # In-memory repo or custom list method
        attempts: list[Any] = []
        if hasattr(self.payment_repo, "list_by_status"):
            attempts = self.payment_repo.list_by_status(PaymentStatus.UNKNOWN)
        elif hasattr(self.payment_repo, "_attempts"):
            # in-memory store inspection fallback
            with getattr(self.payment_repo, "_lock", threading.Lock()):
                raw_attempts = list(self.payment_repo._attempts.values())
                if raw_attempts and isinstance(raw_attempts[0], dict):
                    attempts = [
                        a
                        for tenant_attempts in self.payment_repo._attempts.values()
                        for a in tenant_attempts.values()
                        if getattr(a, "status", None) == PaymentStatus.UNKNOWN
                    ]
                else:
                    attempts = [
                        a
                        for a in self.payment_repo._attempts.values()
                        if getattr(a, "status", None) == PaymentStatus.UNKNOWN
                    ]

        for attempt in attempts:
            attempt_time = getattr(attempt, "created_at", now)
            if attempt_time <= cutoff:
                try:
                    self.payment_reconciler.reconcile_timeout(attempt)
                    reconciled += 1
                except Exception as exc:
                    logger.warning(
                        "Reconciliation failed for payment %s: %s", attempt.attempt_id, exc
                    )

        return reconciled

    def _expire_stale_approvals(self) -> int:
        if self.approval_repo is None:
            return 0

        expired_count = 0
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.approval_ttl_seconds)

        approvals: list[Any] = []
        if hasattr(self.approval_repo, "list_pending"):
            approvals = self.approval_repo.list_pending()
        elif hasattr(self.approval_repo, "_approvals"):
            with getattr(self.approval_repo, "_lock", threading.Lock()):
                raw_values = list(self.approval_repo._approvals.values())
                if raw_values and isinstance(raw_values[0], dict):
                    approvals = [
                        app
                        for tenant_apps in self.approval_repo._approvals.values()
                        for app in tenant_apps.values()
                        if getattr(app, "status", None) == ApprovalStatus.PENDING
                    ]
                else:
                    approvals = [
                        app
                        for app in self.approval_repo._approvals.values()
                        if getattr(app, "status", None) == ApprovalStatus.PENDING
                    ]

        for app in approvals:
            req_time = getattr(app, "requested_at", now)
            if req_time <= cutoff:
                try:
                    expired_app = app.expire(decided_at=now)
                    if hasattr(self.approval_repo, "save"):
                        self.approval_repo.save(app.tenant_id, expired_app)
                    expired_count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to expire approval %s: %s",
                        getattr(app, "approval_id", "unknown"),
                        exc,
                    )

        return expired_count

    def start(self, interval_seconds: float = 1.0) -> None:
        """Starts background worker polling thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop, args=(interval_seconds,), daemon=True
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        """Stops background worker polling thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_seconds)

    def _run_loop(self, interval_seconds: float) -> None:
        while self._running:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("Error in background worker loop: %s", exc)
            time.sleep(interval_seconds)

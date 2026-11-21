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
from agentpay.domain.errors import ApprovalConcurrencyError
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
        max_reconciliation_retries: int = 5,
        reconciliation_initial_backoff_seconds: float = 2.0,
        reconciliation_max_backoff_seconds: float = 60.0,
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
        self.max_reconciliation_retries = max_reconciliation_retries
        self.reconciliation_initial_backoff_seconds = reconciliation_initial_backoff_seconds
        self.reconciliation_max_backoff_seconds = reconciliation_max_backoff_seconds
        self._reconciliation_retries: dict[str, int] = {}
        self._reconciliation_next_retry: dict[str, datetime] = {}

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
                attempt_id = getattr(attempt, "attempt_id", "")
                next_retry = self._reconciliation_next_retry.get(attempt_id)
                if next_retry is not None and now < next_retry:
                    continue

                retries = self._reconciliation_retries.get(attempt_id, 0)
                if retries >= self.max_reconciliation_retries:
                    continue

                try:
                    result = self.payment_reconciler.reconcile_timeout(attempt)
                    if getattr(result, "status", None) != PaymentStatus.UNKNOWN:
                        reconciled += 1
                        self._reconciliation_retries.pop(attempt_id, None)
                        self._reconciliation_next_retry.pop(attempt_id, None)
                    else:
                        self._record_reconciliation_failure(attempt_id, now)
                except Exception as exc:
                    self._record_reconciliation_failure(attempt_id, now)
                    logger.warning(
                        "Reconciliation failed for payment %s (attempt %d/%d): %s",
                        attempt_id,
                        self._reconciliation_retries.get(attempt_id, 1),
                        self.max_reconciliation_retries,
                        exc,
                    )

        return reconciled

    def _record_reconciliation_failure(self, attempt_id: str, now: datetime) -> None:
        if not attempt_id:
            return
        retries = self._reconciliation_retries.get(attempt_id, 0) + 1
        self._reconciliation_retries[attempt_id] = retries
        backoff_seconds = min(
            self.reconciliation_initial_backoff_seconds * (2 ** (retries - 1)),
            self.reconciliation_max_backoff_seconds,
        )
        self._reconciliation_next_retry[attempt_id] = now + timedelta(seconds=backoff_seconds)

    def _expire_stale_approvals(self) -> int:
        if self.approval_repo is None:
            return 0

        expired_count = 0
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.approval_ttl_seconds)

        approvals: list[Any] = []
        if hasattr(self.approval_repo, "list_all_pending_for_maintenance"):
            approvals = self.approval_repo.list_all_pending_for_maintenance()
        elif hasattr(self.approval_repo, "list_pending"):
            try:
                approvals = self.approval_repo.list_pending()
            except TypeError:
                approvals = []
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
                        try:
                            self.approval_repo.save(
                                app.tenant_id,
                                expired_app,
                                expected_status=ApprovalStatus.PENDING,
                            )
                            expired_count += 1
                        except TypeError:
                            self.approval_repo.save(app.tenant_id, expired_app)
                            expired_count += 1
                except ApprovalConcurrencyError as exc:
                    logger.debug(
                        "Approval %s resolved concurrently before expiration: %s",
                        getattr(app, "approval_id", "unknown"),
                        exc,
                    )
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

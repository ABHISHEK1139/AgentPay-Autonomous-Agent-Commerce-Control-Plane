"""Abuse Detection and Security Event Mitigation Service (§62, §63)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from agentpay.domain.audit import AuditEventType
from agentpay.domain.identity import AgentStatus
from agentpay.domain.security_event import (
    SecurityActionTaken,
    SecurityEvent,
    SecurityEventType,
    SecuritySeverity,
)

logger = logging.getLogger(__name__)


class AbuseDetector:
    """Detects security anomalies, emits SecurityEvents to the audit ledger, and enforces mitigation (§62, §63)."""

    def __init__(
        self,
        audit_ledger: Any | None = None,
        agent_repo: Any | None = None,
        credential_stuffing_threshold: int = 5,
        credential_stuffing_window_seconds: int = 60,
        scraping_burst_threshold: int = 30,
        scraping_burst_window_seconds: int = 10,
        spend_velocity_threshold_minor_units: int = 500_000,
        spend_velocity_window_seconds: int = 60,
    ) -> None:
        self.audit_ledger = audit_ledger
        self.agent_repo = agent_repo
        self.credential_stuffing_threshold = credential_stuffing_threshold
        self.credential_stuffing_window = timedelta(seconds=credential_stuffing_window_seconds)
        self.scraping_burst_threshold = scraping_burst_threshold
        self.scraping_burst_window = timedelta(seconds=scraping_burst_window_seconds)
        self.spend_velocity_threshold = spend_velocity_threshold_minor_units
        self.spend_velocity_window = timedelta(seconds=spend_velocity_window_seconds)

        self._lock = threading.Lock()
        # client_ip -> list of failure datetimes
        self._auth_failures: dict[str, list[datetime]] = defaultdict(list)
        # (tenant_id, client_ip) -> list of catalog query datetimes
        self._catalog_queries: dict[tuple[str, str], list[datetime]] = defaultdict(list)
        # (tenant_id, agent_id) -> list of (datetime, amount_minor)
        self._spend_records: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)

    def record_auth_failure(
        self, client_ip: str, tenant_id: str = "system"
    ) -> SecurityEvent | None:
        """Records an authentication failure and triggers mitigation if threshold breached."""
        now = datetime.now(UTC)
        with self._lock:
            history = self._auth_failures[client_ip]
            history.append(now)
            # Prune outside window
            cutoff = now - self.credential_stuffing_window
            self._auth_failures[client_ip] = [t for t in history if t >= cutoff]
            count = len(self._auth_failures[client_ip])

        if count >= self.credential_stuffing_threshold:
            event = SecurityEvent(
                tenant_id=tenant_id,
                event_type=SecurityEventType.CREDENTIAL_STUFFING,
                severity=SecuritySeverity.HIGH,
                action_taken=SecurityActionTaken.BLOCK,
                description=f"Credential stuffing detected: {count} failed auth attempts in {self.credential_stuffing_window.total_seconds()}s",
                client_ip=client_ip,
                details={"failure_count": count},
            )
            self._handle_security_event(event)
            return event
        return None

    def record_catalog_query(self, tenant_id: str, client_ip: str) -> SecurityEvent | None:
        """Records a catalog read request and detects scraping bursts."""
        now = datetime.now(UTC)
        key = (tenant_id, client_ip)
        with self._lock:
            history = self._catalog_queries[key]
            history.append(now)
            cutoff = now - self.scraping_burst_window
            self._catalog_queries[key] = [t for t in history if t >= cutoff]
            count = len(self._catalog_queries[key])

        if count >= self.scraping_burst_threshold:
            event = SecurityEvent(
                tenant_id=tenant_id,
                event_type=SecurityEventType.SCRAPING_BURST,
                severity=SecuritySeverity.MEDIUM,
                action_taken=SecurityActionTaken.RATE_LIMIT,
                description=f"Catalog scraping burst detected: {count} queries in {self.scraping_burst_window.total_seconds()}s",
                client_ip=client_ip,
                details={"query_count": count},
            )
            self._handle_security_event(event)
            return event
        return None

    def record_agent_purchase(
        self, tenant_id: str, agent_id: str, amount_minor_units: int
    ) -> SecurityEvent | None:
        """Records agent purchase volume and flags anomalous spend velocity spikes."""
        now = datetime.now(UTC)
        key = (tenant_id, agent_id)
        with self._lock:
            history = self._spend_records[key]
            history.append((now, amount_minor_units))
            cutoff = now - self.spend_velocity_window
            self._spend_records[key] = [(t, amt) for t, amt in history if t >= cutoff]
            total_spent = sum(amt for _, amt in self._spend_records[key])

        if total_spent >= self.spend_velocity_threshold:
            event = SecurityEvent(
                tenant_id=tenant_id,
                agent_id=agent_id,
                event_type=SecurityEventType.SPEND_VELOCITY_SPIKE,
                severity=SecuritySeverity.CRITICAL,
                action_taken=SecurityActionTaken.SUSPEND_AGENT,
                description=f"Spend velocity spike: {total_spent} minor units spent in {self.spend_velocity_window.total_seconds()}s",
                details={
                    "window_spend_minor": total_spent,
                    "threshold": self.spend_velocity_threshold,
                },
            )
            self._handle_security_event(event)
            return event
        return None

    def flag_tool_loop_exhaustion(
        self, tenant_id: str, agent_id: str, loop_count: int, max_budget: int
    ) -> SecurityEvent:
        """Flags runaway tool calling loop (§64) and quarantines agent."""
        event = SecurityEvent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            event_type=SecurityEventType.TOOL_LOOP_EXHAUSTION,
            severity=SecuritySeverity.CRITICAL,
            action_taken=SecurityActionTaken.QUARANTINE_AGENT,
            description=f"Runaway tool loop budget exhausted: {loop_count}/{max_budget} calls",
            details={"loop_count": loop_count, "max_budget": max_budget},
        )
        self._handle_security_event(event)
        return event

    def flag_prompt_injection(
        self, tenant_id: str, agent_id: str, injection_sample: str
    ) -> SecurityEvent:
        """Flags prompt injection in untrusted external text (§25) and quarantines agent."""
        event = SecurityEvent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            event_type=SecurityEventType.PROMPT_INJECTION,
            severity=SecuritySeverity.CRITICAL,
            action_taken=SecurityActionTaken.QUARANTINE_AGENT,
            description="Prompt injection attempt detected in untrusted external text",
            details={"sample_prefix": injection_sample[:80]},
        )
        self._handle_security_event(event)
        return event

    def _handle_security_event(self, event: SecurityEvent) -> None:
        """Appends security event to audit ledger and applies agent isolation."""
        logger.warning(
            "SECURITY EVENT DETECTED [%s] - %s: %s (Action: %s)",
            event.severity.value,
            event.event_type.value,
            event.description,
            event.action_taken.value,
        )

        # 1. Append to hash-chained audit ledger if provided
        if self.audit_ledger:
            try:
                if hasattr(self.audit_ledger, "record_event"):
                    self.audit_ledger.record_event(
                        event_type=AuditEventType.SECURITY_ALERT,
                        action_id=None,
                        tenant_id=event.tenant_id,
                        actor="abuse_detector",
                        data=event.to_audit_payload(),
                        agent_id=event.agent_id,
                    )
            except Exception as exc:
                logger.error("Failed to append security event to audit ledger: %s", exc)

        # 2. Mitigate agent if action is QUARANTINE or SUSPEND
        if event.agent_id and self.agent_repo:
            try:
                target_status = (
                    AgentStatus.QUARANTINED
                    if event.action_taken == SecurityActionTaken.QUARANTINE_AGENT
                    else AgentStatus.SUSPENDED
                )
                agent = self.agent_repo.get(event.tenant_id, event.agent_id)
                if agent:
                    updated_agent = agent.transition_to(target_status)
                    if hasattr(self.agent_repo, "save"):
                        self.agent_repo.save(event.tenant_id, updated_agent)
                    logger.info(
                        "Agent %s transitioned to %s due to security action",
                        event.agent_id,
                        target_status.value,
                    )
            except Exception as exc:
                logger.error("Failed to update agent status during abuse mitigation: %s", exc)

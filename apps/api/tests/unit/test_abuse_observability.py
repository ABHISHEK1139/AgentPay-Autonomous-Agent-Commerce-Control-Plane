"""Comprehensive tests for Abuse Detection, Security Events, and Observability (§47, §62, §63)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from agentpay.adapters.in_memory.audit_ledger import InMemoryAuditLedger
from agentpay.adapters.in_memory.identity_repository import (
    InMemoryAgentRepository,
)
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.application.services.abuse_detector import AbuseDetector
from agentpay.domain.identity import Agent, AgentStatus
from agentpay.domain.security_event import (
    SecurityActionTaken,
    SecurityEvent,
    SecurityEventType,
    SecuritySeverity,
)
from agentpay.infrastructure.observability import (
    Meter,
    Tracer,
    get_meter,
)


def test_security_event_domain_model() -> None:
    event = SecurityEvent(
        tenant_id="tenant_01",
        event_type=SecurityEventType.PROMPT_INJECTION,
        severity=SecuritySeverity.CRITICAL,
        action_taken=SecurityActionTaken.QUARANTINE_AGENT,
        description="Prompt injection in merchant copy",
        agent_id="agent_rogue",
        details={"model": "gpt-4"},
    )
    assert event.severity == SecuritySeverity.CRITICAL
    assert event.action_taken == SecurityActionTaken.QUARANTINE_AGENT

    payload = event.to_audit_payload()
    assert payload["security_event_type"] == "PROMPT_INJECTION"
    assert payload["agent_id"] == "agent_rogue"
    assert payload["details"] == {"model": "gpt-4"}


def test_abuse_detector_credential_stuffing() -> None:
    audit_ledger = InMemoryAuditLedger()
    detector = AbuseDetector(
        audit_ledger=audit_ledger,
        credential_stuffing_threshold=3,
        credential_stuffing_window_seconds=10,
    )

    ip = "198.51.100.42"
    # First 2 attempts: no threshold breach
    assert detector.record_auth_failure(ip) is None
    assert detector.record_auth_failure(ip) is None

    # Third attempt breaches threshold
    event = detector.record_auth_failure(ip)
    assert event is not None
    assert event.event_type == SecurityEventType.CREDENTIAL_STUFFING
    assert event.severity == SecuritySeverity.HIGH
    assert event.action_taken == SecurityActionTaken.BLOCK

    # Verify audit event appended to ledger
    events = audit_ledger.get_events("system")
    assert len(events) == 1
    assert events[0].data["security_event_type"] == "CREDENTIAL_STUFFING"


def test_abuse_detector_scraping_burst() -> None:
    detector = AbuseDetector(
        scraping_burst_threshold=5,
        scraping_burst_window_seconds=10,
    )
    ip = "203.0.113.1"
    tenant = "tenant_shop"

    for _ in range(4):
        assert detector.record_catalog_query(tenant, ip) is None

    event = detector.record_catalog_query(tenant, ip)
    assert event is not None
    assert event.event_type == SecurityEventType.SCRAPING_BURST
    assert event.action_taken == SecurityActionTaken.RATE_LIMIT


def test_abuse_detector_spend_velocity_and_agent_suspension() -> None:
    agent_repo = InMemoryAgentRepository()
    now = datetime.now(UTC)
    agent = Agent(
        agent_id="agent_fast",
        tenant_id="tenant_sp",
        principal_id="user_sp",
        issuer="test_suite",
        capabilities=("commerce",),
        scopes=("purchase",),
        created_at=now,
        expires_at=now + timedelta(days=1),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save("tenant_sp", agent)

    detector = AbuseDetector(
        agent_repo=agent_repo,
        spend_velocity_threshold_minor_units=100_000,
        spend_velocity_window_seconds=60,
    )

    # First purchase: ₹600.00
    assert detector.record_agent_purchase("tenant_sp", "agent_fast", 60_000) is None
    assert agent_repo.get("tenant_sp", "agent_fast").status == AgentStatus.ACTIVE  # type: ignore[union-attr]

    # Second purchase pushes to ₹1,100.00 -> breaches ₹1,000 threshold
    event = detector.record_agent_purchase("tenant_sp", "agent_fast", 50_000)
    assert event is not None
    assert event.event_type == SecurityEventType.SPEND_VELOCITY_SPIKE
    assert event.action_taken == SecurityActionTaken.SUSPEND_AGENT

    # Agent is automatically suspended
    updated_agent = agent_repo.get("tenant_sp", "agent_fast")
    assert updated_agent is not None
    assert updated_agent.status == AgentStatus.SUSPENDED


def test_abuse_detector_tool_loop_and_quarantine() -> None:
    agent_repo = InMemoryAgentRepository()
    now = datetime.now(UTC)
    agent = Agent(
        agent_id="agent_looping",
        tenant_id="tenant_lp",
        principal_id="user_lp",
        issuer="test_suite",
        capabilities=("commerce",),
        scopes=("purchase",),
        created_at=now,
        expires_at=now + timedelta(days=1),
        status=AgentStatus.ACTIVE,
    )
    agent_repo.save("tenant_lp", agent)

    detector = AbuseDetector(agent_repo=agent_repo)
    event = detector.flag_tool_loop_exhaustion(
        "tenant_lp", "agent_looping", loop_count=21, max_budget=20
    )
    assert event.event_type == SecurityEventType.TOOL_LOOP_EXHAUSTION
    assert event.action_taken == SecurityActionTaken.QUARANTINE_AGENT

    updated_agent = agent_repo.get("tenant_lp", "agent_looping")
    assert updated_agent is not None
    assert updated_agent.status == AgentStatus.QUARANTINED


def test_tracer_and_span_lifecycle() -> None:
    tracer = Tracer("test_tracer")
    tracer.clear()

    with tracer.start_span("root_operation", attributes={"env": "prod"}) as root_span:
        root_span.set_attribute("user.id", "u123")
        root_span.add_event("cache_miss", {"key": "catalog_1"})

        with tracer.start_span("child_operation") as child_span:
            assert child_span.trace_id == root_span.trace_id
            assert child_span.parent_span_id == root_span.span_id
            child_span.set_attribute("db.table", "orders")

    spans = tracer.list_spans()
    assert len(spans) == 2
    root = spans[0]
    child = spans[1]

    assert root.name == "root_operation"
    assert root.status == "OK"
    assert root.end_time is not None
    assert root.duration_ms >= 0.0
    assert len(root.events) == 1

    assert child.name == "child_operation"
    assert child.trace_id == root.trace_id


def test_tracer_error_capture() -> None:
    tracer = Tracer("test_error_tracer")
    try:
        with tracer.start_span("failing_op"):
            raise RuntimeError("Database connection lost")
    except RuntimeError:
        pass

    spans = tracer.list_spans()
    assert len(spans) == 1
    assert spans[0].status == "ERROR"
    assert "Database connection lost" in spans[0].attributes["error.message"]


def test_meter_counter_and_histogram() -> None:
    meter = Meter("test_meter")
    counter = meter.create_counter("http_requests_total", "Total requests")
    counter.increment(1.0, {"method": "GET", "status": "200"})
    counter.increment(2.0, {"method": "GET", "status": "200"})
    counter.increment(1.0, {"method": "POST", "status": "400"})

    assert counter.get_value({"method": "GET", "status": "200"}) == 3.0
    assert counter.get_value({"method": "POST", "status": "400"}) == 1.0

    histogram = meter.create_histogram("request_latency_seconds", "Latency")
    histogram.record(0.02, {"route": "/actions"})
    histogram.record(0.08, {"route": "/actions"})
    histogram.record(0.50, {"route": "/actions"})

    prom_text = meter.to_prometheus()
    assert "# TYPE http_requests_total counter" in prom_text
    assert 'http_requests_total{method="GET",status="200"} 3.0' in prom_text
    assert "# TYPE request_latency_seconds histogram" in prom_text
    assert "request_latency_seconds_sum" in prom_text


def test_api_metrics_endpoint() -> None:
    from agentpay.adapters.in_memory.payment_repository import InMemoryIdempotencyStore

    orch = MagicMock()
    app = AgentPayApiApp(orchestrator=orch, idempotency_store=InMemoryIdempotencyStore())
    client = ApiTestClient(app)

    # Seed a metric
    meter = get_meter()
    test_counter = meter.create_counter("api_test_events_total")
    test_counter.increment(5.0, {"env": "test"})

    status_code, body = client.get("/api/v1/metrics")
    assert status_code == 200
    assert "metrics" in body
    assert "prometheus" in body
    assert "api_test_events_total" in body["prometheus"]

"""Zero-dependency OpenTelemetry-compatible tracing infrastructure (§47)."""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Span:
    """Individual execution span record."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None
    status: str = "OK"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        """Sets a span attribute."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Adds an event to the span."""
        self.events.append(
            {
                "name": name,
                "timestamp": datetime.now(UTC).isoformat(),
                "attributes": attributes or {},
            }
        )

    def set_status(self, status: str, description: str | None = None) -> None:
        """Sets span status (OK, ERROR)."""
        self.status = status
        if description:
            self.attributes["error.message"] = description

    def finish(self) -> None:
        """Finishes the span."""
        if self.end_time is None:
            self.end_time = datetime.now(UTC)

    @property
    def duration_ms(self) -> float:
        """Calculates span duration in milliseconds."""
        end = self.end_time or datetime.now(UTC)
        return (end - self.start_time).total_seconds() * 1000.0

    def __enter__(self) -> Span:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val is not None:
            self.set_status("ERROR", str(exc_val))
        self.finish()


_CURRENT_SPAN: ContextVar[Span | None] = ContextVar("current_span", default=None)
_CURRENT_TRACE_ID: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


class Tracer:
    """Zero-dependency tracer for distributed context propagation (§47)."""

    def __init__(self, name: str = "agentpay") -> None:
        self.name = name
        self._completed_spans: list[Span] = []

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Starts a new span. Re-uses trace_id from current context if omitted."""
        active_parent = _CURRENT_SPAN.get()

        if trace_id is None:
            trace_id = _CURRENT_TRACE_ID.get() or (
                active_parent.trace_id if active_parent else f"trc_{secrets.token_hex(16)}"
            )

        if parent_span_id is None and active_parent:
            parent_span_id = active_parent.span_id

        span_id = f"spn_{secrets.token_hex(8)}"
        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            attributes=attributes or {},
        )

        _CURRENT_SPAN.set(span)
        _CURRENT_TRACE_ID.set(trace_id)
        self._completed_spans.append(span)
        return span

    def get_current_span(self) -> Span | None:
        """Returns the currently active span."""
        return _CURRENT_SPAN.get()

    def get_current_trace_id(self) -> str | None:
        """Returns current active trace ID."""
        return _CURRENT_TRACE_ID.get()

    def list_spans(self) -> list[Span]:
        """Returns all completed spans."""
        return list(self._completed_spans)

    def clear(self) -> None:
        """Clears completed spans and context."""
        self._completed_spans.clear()
        _CURRENT_SPAN.set(None)
        _CURRENT_TRACE_ID.set(None)

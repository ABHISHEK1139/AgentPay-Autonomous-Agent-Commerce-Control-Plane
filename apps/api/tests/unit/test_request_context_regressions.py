"""Request metadata must remain isolated and be cleaned up on rejection."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest

from agentpay.api.app import MAX_REQUEST_BODY_BYTES, _current_trace_id
from agentpay.api.factory import create_app


def test_concurrent_requests_keep_their_own_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    barrier = Barrier(2)

    def dispatch(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        barrier.wait(timeout=5)
        return 200, {}

    monkeypatch.setattr(app, "_dispatch", dispatch)

    def request(identity: str) -> str:
        response = app.handle_request("GET", "/api/v1/health", {"x-request-id": identity})
        return response.headers["x-request-id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(request, ["first", "second"])) == ["first", "second"]


@pytest.mark.parametrize("raw", [True, False])
def test_oversized_request_restores_trace_context(raw: bool) -> None:
    app = create_app()
    token = _current_trace_id.set("outer-trace")
    try:
        response = app.handle_request(
            "POST",
            "/api/v1/actions",
            {"content-length": str(MAX_REQUEST_BODY_BYTES + 1), "x-trace-id": "inner-trace"},
            raw_body=b"x" * (MAX_REQUEST_BODY_BYTES + 1) if raw else None,
        )
        assert response.status_code == 413
        assert _current_trace_id.get() == "outer-trace"
    finally:
        _current_trace_id.reset(token)

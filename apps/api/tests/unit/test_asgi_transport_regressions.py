"""ASGI must preserve signed bytes and bound request buffering."""

import asyncio
from typing import Any

import pytest

from agentpay.api.app import MAX_REQUEST_BODY_BYTES, ApiResponse
from agentpay.api.factory import create_app


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("oversized", [False, True])
def test_asgi_preserves_bytes_and_bounds_body(
    monkeypatch: pytest.MonkeyPatch, oversized: bool, chunked: bool
) -> None:
    app = create_app()
    raw = b'{ "event": "test" }'
    if oversized:
        raw = b" " * (MAX_REQUEST_BODY_BYTES + 1)
    captured: list[bytes | None] = []
    sent: list[dict[str, Any]] = []

    def dispatch(*args: Any, **kwargs: Any) -> ApiResponse:
        captured.append(kwargs.get("raw_body"))
        return ApiResponse(200, {})

    monkeypatch.setattr(app, "handle_request", dispatch)

    chunks = [raw[: len(raw) // 2], raw[len(raw) // 2 :]] if chunked else [raw]

    async def receive() -> dict[str, Any]:
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(
        app({"type": "http", "method": "POST", "path": "/api/v1/webhooks/payments"}, receive, send)
    )
    if oversized:
        assert sent[0]["status"] == 413
        assert captured == []
    else:
        assert sent[0]["status"] == 200
        assert captured == [raw]

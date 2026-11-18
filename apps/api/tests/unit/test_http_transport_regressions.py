"""Regression coverage for HTTP response isolation and browser retry headers."""

from __future__ import annotations

import threading
from http.client import HTTPConnection
from typing import Any

import pytest

from agentpay.api.app import ApiResponse
from agentpay.api.factory import create_app
from agentpay.server import create_server


@pytest.mark.parametrize("scenario", ["response_headers", "idempotency_preflight"])
def test_http_transport_preserves_request_contract(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    app = create_app()

    def dispatch(*args: Any, **kwargs: Any) -> ApiResponse:
        response = ApiResponse(200, {"status": "ok"}, {"x-request-id": "original-request"})
        # Deterministically simulate another request updating shared state after
        # this response was constructed, before the transport serializes it.
        app._last_response_headers = {"x-request-id": "other-request"}
        return response

    monkeypatch.setattr(app, "handle_request", dispatch)
    server = create_server(host="127.0.0.1", port=0, app=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        if scenario == "response_headers":
            connection.request("GET", "/api/v1/health")
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("x-request-id") == "original-request"
        else:
            connection.request(
                "OPTIONS",
                "/api/v1/actions",
                headers={
                    "Origin": "https://buyer.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Idempotency-Key",
                },
            )
            response = connection.getresponse()
            assert response.status == 204
            allowed = response.getheader("Access-Control-Allow-Headers", "")
            assert "idempotency-key" in {value.strip().lower() for value in allowed.split(",")}
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

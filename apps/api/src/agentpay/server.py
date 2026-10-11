"""Built-in HTTP Server Runner for AgentPay (§10, §12, §13).

Provides a zero-dependency, multi-threaded HTTP server using Python's standard library
`http.server.ThreadingHTTPServer`. Handles CORS pre-flight, security headers,
request body parsing, API dispatching, and optional Single Page Application (SPA) static file serving.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentpay.api.app import AgentPayApiApp
from agentpay.api.factory import create_app

logger = logging.getLogger("agentpay.server")

MAX_REQUEST_BODY_BYTES: int = 1024 * 1024  # 1 MiB payload limit (§62, B-SRV-01)


def create_request_handler(
    app: AgentPayApiApp,
    static_dir: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Factory creating an HTTP request handler bound to the specified application and static directory."""

    class AgentPayHttpHandler(BaseHTTPRequestHandler):
        server_version = "AgentPay/0.1.0"

        def log_message(self, format_str: str, *args: Any) -> None:
            logger.info(
                "%s - - [%s] " + format_str,
                self.address_string(),
                self.log_date_time_string(),
                *args,
            )

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, HEAD"
            )
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-Tenant-ID, X-API-Key, X-Request-ID, "
                "X-Webhook-Signature, X-Webhook-Timestamp, Accept",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors_headers()
            self.end_headers()

        def do_GET(self) -> None:
            self._handle_request("GET")

        def do_POST(self) -> None:
            self._handle_request("POST")

        def do_PUT(self) -> None:
            self._handle_request("PUT")

        def do_DELETE(self) -> None:
            self._handle_request("DELETE")

        def _handle_request(self, method: str) -> None:
            clean_path = self.path.split("?")[0]

            # 1. Check if this is an API route or OpenAPI/Metrics route
            is_api_route = (
                clean_path.startswith("/api/")
                or clean_path.startswith("/agent/")
                or clean_path == "/openapi.json"
                or clean_path == "/metrics"
            )

            # 2. If not an API route and static_dir is provided, try serving static assets
            if (
                not is_api_route
                and static_dir
                and static_dir.exists()
                and self._try_serve_static(clean_path)
            ):
                return

            # 3. Read request body if present (§62, B-SRV-01)
            content_length_str = self.headers.get("Content-Length", "0")
            try:
                content_length = int(content_length_str)
            except ValueError:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                err_resp = {
                    "error": {
                        "code": "INVALID_CONTENT_LENGTH",
                        "message": "Content-Length header must be a valid non-negative integer",
                        "request_id": self.headers.get("x-request-id", "req_invalid_cl"),
                    }
                }
                self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                return

            if content_length > MAX_REQUEST_BODY_BYTES:
                self.send_response(413)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                err_resp = {
                    "error": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds maximum permitted size of {MAX_REQUEST_BODY_BYTES} bytes",
                        "request_id": self.headers.get("x-request-id", "req_payload_too_large"),
                    }
                }
                self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                return

            json_body: dict[str, Any] | None = None
            raw_bytes: bytes | None = None
            if content_length > 0:
                raw_bytes = self.rfile.read(content_length)
                try:
                    json_body = json.loads(raw_bytes.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.send_response(422)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    err_resp = {
                        "error": {
                            "code": "INVALID_REQUEST_BODY",
                            "message": "Request body must be valid JSON",
                            "request_id": self.headers.get("x-request-id", "req_invalid_json"),
                        }
                    }
                    self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                    return

            # 4. Convert headers to dictionary
            headers_dict = {k.lower(): v for k, v in self.headers.items()}

            # 5. Dispatch to AgentPay application
            status_code, response_body = app.handle_request(
                method, self.path, headers_dict, json_body, raw_body=raw_bytes
            )

            # 6. Write HTTP response
            resp_bytes = json.dumps(response_body).encode("utf-8")
            self.send_response(status_code)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))

            for hk, hv in app.last_response_headers.items():
                self.send_header(hk, hv)

            self.end_headers()
            self.wfile.write(resp_bytes)

        def _try_serve_static(self, clean_path: str) -> bool:
            if static_dir is None:
                return False
            # Normalize path
            relative_path = clean_path.lstrip("/")
            target_file = static_dir / relative_path

            # If directory or root, serve index.html
            if clean_path in ("", "/"):
                target_file = static_dir / "index.html"
            elif not target_file.exists() or target_file.is_dir():
                # SPA fallback: if file does not have an extension, serve index.html
                if "." not in clean_path.split("/")[-1]:
                    target_file = static_dir / "index.html"
                else:
                    return False

            if not target_file.exists() or not target_file.is_file():
                return False

            # Prevent directory traversal
            try:
                target_file.resolve().relative_to(static_dir.resolve())
            except ValueError:
                return False

            content_type, _ = mimetypes.guess_type(str(target_file))
            if not content_type:
                content_type = "application/octet-stream"

            file_bytes = target_file.read_bytes()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(file_bytes)))
            self.end_headers()
            self.wfile.write(file_bytes)
            return True

    return AgentPayHttpHandler


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    app: AgentPayApiApp | None = None,
    static_dir: Path | str | None = None,
    database_url: str | None = None,
) -> ThreadingHTTPServer:
    """Create a configured multi-threaded HTTP server instance."""
    app_instance = app or create_app(database_url=database_url)
    resolved_static = Path(static_dir) if static_dir else None

    handler_cls = create_request_handler(app_instance, resolved_static)
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    app: AgentPayApiApp | None = None,
    static_dir: Path | str | None = None,
    database_url: str | None = None,
) -> None:
    """Run the AgentPay HTTP server until interrupted."""
    server = create_server(
        host=host,
        port=port,
        app=app,
        static_dir=static_dir,
        database_url=database_url,
    )
    logger.info("AgentPay Control Plane running on http://%s:%s", host, port)
    logger.info("Health check: http://%s:%s/api/v1/health", host, port)
    logger.info("OpenAPI specification: http://%s:%s/openapi.json", host, port)
    if static_dir and Path(static_dir).exists():
        logger.info("Web UI Control Center: http://%s:%s/", host, port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down AgentPay server...")
    finally:
        server.server_close()
